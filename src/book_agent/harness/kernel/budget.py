"""Per-turn budget: steps, tool calls, tokens, cost."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TurnBudget:
    max_steps: int = 20
    max_tool_calls: int = 60
    max_total_tokens: int | None = None
    max_cost_usd: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_total_tokens": self.max_total_tokens,
            "max_cost_usd": self.max_cost_usd,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any] | None) -> "TurnBudget":
        payload = payload or {}
        return cls(
            max_steps=int(payload.get("max_steps", cls.max_steps)),
            max_tool_calls=int(payload.get("max_tool_calls", cls.max_tool_calls)),
            max_total_tokens=payload.get("max_total_tokens"),
            max_cost_usd=payload.get("max_cost_usd"),
        )

    def exceeded(self, usage: dict[str, Any]) -> str | None:
        """The budget dimension that is exhausted, or None."""
        if int(usage.get("steps", 0)) >= self.max_steps:
            return "budget.max_steps"
        if int(usage.get("tool_calls", 0)) >= self.max_tool_calls:
            return "budget.max_tool_calls"
        if self.max_total_tokens is not None:
            total = int(usage.get("token_in", 0)) + int(usage.get("token_out", 0))
            if total >= self.max_total_tokens:
                return "budget.max_total_tokens"
        if self.max_cost_usd is not None and float(usage.get("cost_usd", 0.0)) >= float(self.max_cost_usd):
            return "budget.max_cost_usd"
        return None
