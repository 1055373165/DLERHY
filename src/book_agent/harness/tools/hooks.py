"""Deterministic hooks around tool calls (the code-level guardrails)."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from book_agent.domain.enums import ActorType
from book_agent.domain.models.ops import AuditEvent
from book_agent.harness.tools.registry import ToolContext, ToolOutcome, ToolRejected, ToolSpec


class ToolHook(Protocol):
    def before_tool(self, ctx: ToolContext, tool: ToolSpec, arguments: BaseModel) -> None:
        """Raise ToolRejected to block the call."""

    def after_tool(self, ctx: ToolContext, tool: ToolSpec, arguments: BaseModel, outcome: ToolOutcome) -> None: ...


class ToolCallLimitHook:
    """Enforces ``ToolSpec.max_calls_per_turn`` using the counts the runner keeps in ``ctx.extras``."""

    def before_tool(self, ctx: ToolContext, tool: ToolSpec, arguments: BaseModel) -> None:
        if tool.max_calls_per_turn is None:
            return
        counts: dict[str, int] = ctx.extras.setdefault("tool_call_counts", {})
        if counts.get(tool.name, 0) >= tool.max_calls_per_turn:
            raise ToolRejected(f"{tool.name} may be called at most {tool.max_calls_per_turn} times per turn")

    def after_tool(self, ctx: ToolContext, tool: ToolSpec, arguments: BaseModel, outcome: ToolOutcome) -> None:
        counts: dict[str, int] = ctx.extras.setdefault("tool_call_counts", {})
        counts[tool.name] = counts.get(tool.name, 0) + 1


class AuditHook:
    """Every tool call lands in audit_events, whether it succeeded or not."""

    def before_tool(self, ctx: ToolContext, tool: ToolSpec, arguments: BaseModel) -> None:
        return None

    def after_tool(self, ctx: ToolContext, tool: ToolSpec, arguments: BaseModel, outcome: ToolOutcome) -> None:
        payload: dict[str, Any] = {
            "tool": tool.name,
            "permission": tool.permission.value,
            "arguments": arguments.model_dump(mode="json"),
            "ok": outcome.ok,
        }
        if outcome.error:
            payload["error"] = outcome.error[:500]
        ctx.session.add(
            AuditEvent(
                object_type="agent_turn",
                object_id=ctx.turn_id,
                action=f"tool.{tool.name}",
                actor_type=ActorType.MODEL,
                actor_id=f"agent.{ctx.agent_kind}",
                payload_json=payload,
            )
        )
        ctx.session.flush()


DEFAULT_HOOKS: tuple[ToolHook, ...] = (ToolCallLimitHook(), AuditHook())
