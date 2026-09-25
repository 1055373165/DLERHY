"""Pilot repair agent: the one judgment the pilot repair loop cannot make by rule.

docs/agent-upgrade/08-pilot-quality-gate.md §7.2. The loop (services/pilot_repair.py) repairs by
rule wherever the finding says what to do. For a few structure problems the rules cannot place the
edit (where a caption ends and the body begins, where a heading glued to a paragraph stops) it asks
this agent for one proposal: split a block at a given text, relabel it, or hide it. The agent only
reads and proposes; the loop applies the proposal through ``StructureEditService`` in a savepoint
and keeps it only when the layered QA says it helped, exactly like a rule's repair.

Each call is a short turn in the agent ledger (kind ``pilot_repair``) with a small budget; a turn
that runs out of budget is cancelled, never left waiting for a person.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from book_agent.domain.enums import AgentItemKind, AgentTurnStatus, ApprovalStatus
from book_agent.domain.models.agent import AgentTurn, Approval
from book_agent.harness.kernel.budget import TurnBudget
from book_agent.harness.kernel.turn import AgentTurnRunner
from book_agent.harness.tools.permissions import PermissionPolicy
from book_agent.harness.tools.registry import ToolContext, ToolError, ToolPermission, ToolRegistry, ToolSpec
from book_agent.infra.db.session import session_scope
from book_agent.infra.repositories.agent import AgentLedgerRepository

logger = logging.getLogger(__name__)

AGENT_KIND = "pilot_repair"
PROPOSAL_TOOL = "propose_structure_edit"
DEFAULT_BUDGET = TurnBudget(max_steps=3, max_tool_calls=3, max_total_tokens=12_000, max_cost_usd=0.01)
ALLOWED_TYPES = {"heading", "paragraph", "caption", "code", "list_item", "quote", "footnote"}

SYSTEM_PROMPT = """You repair the structure of a parsed book page for an English-to-Chinese translation.
A quality check found a problem in one block. You see the block and its neighbours (their ids, types
and text). Propose exactly ONE edit with propose_structure_edit, then reply with one short line and no
more tool calls:
- split: the block holds two things (a caption and the body text after it; a run-in heading and its
  paragraph). Give second_part_starts_with: the exact text where the second part begins (at least 12
  characters, copied from the block, occurring once) and, if the second part is another kind of block,
  second_part_type (paragraph, heading, ...).
- relabel: the block is the wrong kind (a sentence set as a heading, a code comment set as text).
- hide: the block is page furniture the reader must not see (a running header, a figure's own label).
If no edit would fix it, reply without a tool call."""


class ProposeStructureEditArgs(BaseModel):
    kind: str = Field(description="split | relabel | hide")
    block_id: str = Field(description="The block to edit (one of the blocks shown).")
    second_part_starts_with: str = Field(default="", description="split: exact text where the second part starts.")
    second_part_type: str = Field(default="", description="split: block type of the second part, or empty to keep the type.")
    block_type: str = Field(default="", description="relabel: the new block type.")
    reason: str = Field(default="", description="Why, in Chinese, one sentence.")


def propose_structure_edit(ctx: ToolContext, args: ProposeStructureEditArgs) -> dict[str, Any]:
    allowed_blocks = set(ctx.extras.get("block_ids") or [])
    if args.block_id not in allowed_blocks:
        raise ToolError(f"block_id must be one of {sorted(allowed_blocks)}")
    kind = args.kind.strip().lower()
    if kind not in {"split", "relabel", "hide"}:
        raise ToolError("kind must be split, relabel or hide")
    if kind == "split" and len(args.second_part_starts_with.strip()) < 12:
        raise ToolError("second_part_starts_with needs at least 12 characters copied from the block")
    if kind == "relabel" and args.block_type not in ALLOWED_TYPES:
        raise ToolError(f"block_type must be one of {sorted(ALLOWED_TYPES)}")
    if args.second_part_type and args.second_part_type not in ALLOWED_TYPES:
        raise ToolError(f"second_part_type must be one of {sorted(ALLOWED_TYPES)}")
    return {"recorded": True, "proposal": {**args.model_dump(), "kind": kind}, "next": "Reply with one short line; no more tool calls."}


def pilot_repair_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                PROPOSAL_TOOL,
                "Propose one structure edit for the block the check flagged (the repair loop applies and verifies it).",
                ProposeStructureEditArgs,
                ToolPermission.READ,
                propose_structure_edit,
                max_calls_per_turn=2,
            )
        ]
    )


@dataclass(slots=True)
class AgentStructureJudge:
    """``StructureJudge`` backed by an agent turn."""

    session_factory: sessionmaker
    model: Any
    model_name: str
    run_id: str | None = None
    budget: TurnBudget = field(default_factory=lambda: DEFAULT_BUDGET)

    def propose(self, document_id: str, finding: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
        with session_scope(self.session_factory) as session:
            ledger = AgentLedgerRepository(session)
            target = next((block for block in blocks if block.get("is_target")), blocks[0] if blocks else {})
            turn = ledger.create_turn(
                document_id=document_id,
                agent_kind=AGENT_KIND,
                scope_type="block",
                scope_id=str(target.get("block_id") or "") or None,
                run_id=self.run_id,
                model_name=self.model_name,
                budget=self.budget.to_json(),
                skills=["pilot_repair/structure"],
            )
            ledger.append_item(turn.id, kind=AgentItemKind.SYSTEM, content={"text": SYSTEM_PROMPT})
            brief = {
                "problem": finding.get("message"),
                "check": finding.get("check_id"),
                "page": (finding.get("location") or {}).get("page"),
                "blocks": blocks,
            }
            ledger.append_item(turn.id, kind=AgentItemKind.USER, content={"text": json.dumps(brief, ensure_ascii=False)})
            turn_id = turn.id
        runner = AgentTurnRunner(
            session_factory=self.session_factory,
            model=self.model,
            registry=pilot_repair_registry(),
            policy=PermissionPolicy(),
            compaction_threshold_tokens=None,
            tool_extras={"block_ids": [str(block.get("block_id")) for block in blocks]},
        )
        try:
            runner.run(turn_id)
        except Exception:
            logger.warning("pilot repair agent turn %s failed", turn_id, exc_info=True)
            try:
                runner.fail(turn_id, error=RuntimeError("pilot repair agent failed"))
            except Exception:
                pass
        return self._proposal(turn_id)

    def _proposal(self, turn_id: str) -> dict[str, Any] | None:
        with session_scope(self.session_factory) as session:
            ledger = AgentLedgerRepository(session)
            proposal = None
            for item in ledger.list_items(turn_id):
                content = item.content_json or {}
                if item.kind == AgentItemKind.TOOL_RESULT and content.get("name") == PROPOSAL_TOOL:
                    output = (content.get("result") or {}).get("output") or {}
                    if output.get("proposal"):
                        proposal = dict(output["proposal"])
            turn = session.get(AgentTurn, turn_id)
            # Never leave a person a budget question for this: the loop moves on without a proposal.
            if turn is not None and turn.status in {AgentTurnStatus.PAUSED, AgentTurnStatus.AWAITING_APPROVAL, AgentTurnStatus.RUNNING}:
                for approval in session.query(Approval).filter(Approval.turn_id == turn_id, Approval.status == ApprovalStatus.PENDING):
                    approval.status = ApprovalStatus.REJECTED
                    approval.decided_by = "agent.pilot_repair"
                    approval.decision_note = "the pilot repair loop does not wait for a person"
                turn.status = AgentTurnStatus.CANCELLED
                turn.stop_reason = turn.stop_reason or "pilot_repair.no_wait"
            return proposal
