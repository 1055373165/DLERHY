"""Who may run which tool, and when a human has to say yes first."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from book_agent.harness.tools.registry import ToolContext, ToolPermission, ToolSpec


class PermissionKind(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    kind: PermissionKind
    policy_id: str
    reason: str = ""
    # True when an irreversible call was allowed by an auto-approval rule;
    # the runner records an AUTO_APPROVED approval so the decision is auditable.
    auto_approved: bool = False


@dataclass(frozen=True, slots=True)
class AutoApproveRule:
    """Turns a REQUIRE_APPROVAL decision into an auto-approved one when it matches."""

    policy_id: str
    tool_name: str
    predicate: Callable[[ToolContext, BaseModel], bool]
    description: str = ""


@dataclass(slots=True)
class PermissionPolicy:
    """Default tiers: reads and reversible writes run; irreversible writes need approval.

    ``denied_tools`` blocks tools outright for a given agent configuration;
    ``auto_approve`` lists rules that pre-approve specific irreversible calls
    (for example locking a person name as a glossary term).
    """

    auto_approve: list[AutoApproveRule] = field(default_factory=list)
    denied_tools: frozenset[str] = frozenset()
    require_approval_for_reversible: bool = False

    def decide(self, tool: ToolSpec, ctx: ToolContext, arguments: BaseModel) -> PermissionDecision:
        if tool.name in self.denied_tools:
            return PermissionDecision(PermissionKind.DENY, "policy.denied_tool", f"{tool.name} is disabled for {ctx.agent_kind}")
        if tool.permission == ToolPermission.READ:
            return PermissionDecision(PermissionKind.ALLOW, "policy.read")
        if tool.permission == ToolPermission.WRITE_REVERSIBLE and not self.require_approval_for_reversible:
            return PermissionDecision(PermissionKind.ALLOW, "policy.write_reversible")
        for rule in self.auto_approve:
            if rule.tool_name == tool.name and rule.predicate(ctx, arguments):
                return PermissionDecision(
                    PermissionKind.ALLOW, rule.policy_id, rule.description or "auto-approved", auto_approved=True
                )
        return PermissionDecision(
            PermissionKind.REQUIRE_APPROVAL, "policy.write_irreversible", f"{tool.name} changes the book irreversibly"
        )


def describe_arguments(arguments: BaseModel) -> dict[str, Any]:
    return arguments.model_dump(mode="json")
