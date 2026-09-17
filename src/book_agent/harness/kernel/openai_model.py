"""Model adapters for the kernel: the OpenAI-compatible client, and an echo stand-in."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from book_agent.harness.kernel.model import AgentStep, ToolCall


@dataclass(slots=True)
class OpenAICompatibleAgentModel:
    """Drives ``OpenAICompatibleTranslationClient.generate_agent_step``."""

    client: Any  # OpenAICompatibleTranslationClient (kept loose to avoid the import cycle)

    def step(self, *, model_name: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AgentStep:
        result = self.client.generate_agent_step(model_name=model_name, messages=messages, tools=tools)
        return AgentStep(
            text=result.get("text"),
            tool_calls=[
                ToolCall(call_id=str(call["call_id"]), name=str(call["name"]), arguments=dict(call.get("arguments") or {}))
                for call in result.get("tool_calls") or []
            ],
            usage=result.get("usage"),
            finish_reason=result.get("finish_reason"),
            raw=result.get("raw") or {},
        )


@dataclass(slots=True)
class EchoAgentModel:
    """Finishes every turn immediately; pairs with the echo translation worker.

    Lets pipelines that never contact a provider (tests, smoke runs) pass
    through agent stages deterministically.
    """

    final_text: str = "echo agent: nothing to do"

    def step(self, *, model_name: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AgentStep:
        return AgentStep(text=self.final_text, tool_calls=[], usage=None, finish_reason="stop")


def agent_model_for_worker(worker: Any):
    """The agent model that shares the translation worker's provider, or echo."""
    client = getattr(worker, "client", None) if worker is not None else None
    if client is not None and hasattr(client, "generate_agent_step"):
        return OpenAICompatibleAgentModel(client=client)
    return EchoAgentModel()
