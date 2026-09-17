"""What the kernel needs from a model: one step of chat with function tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from book_agent.translation.contracts import TranslationUsage


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class AgentStep:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TranslationUsage | None = None
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class AgentModelClient(Protocol):
    def step(self, *, model_name: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AgentStep:
        """Sample one assistant message given OpenAI-style chat messages and function tools."""
