"""Typed tools: the only way a model changes the ledger.

Each tool declares a pydantic input model (which doubles as the JSON schema
the provider sees), a permission tier and a handler. Handlers get a
``ToolContext`` with the session and the identifiers of the work being done;
they never see the model or the prompt.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session


class ToolPermission(StrEnum):
    READ = "read"
    WRITE_REVERSIBLE = "write_reversible"
    WRITE_IRREVERSIBLE = "write_irreversible"


class ToolRejected(RuntimeError):
    """A hook or policy refused the call; the model sees the reason as the tool result."""


class ToolError(RuntimeError):
    """The tool ran but could not do what was asked; the model sees the reason."""


@dataclass(slots=True)
class ToolContext:
    session: Session
    document_id: str
    agent_kind: str
    turn_id: str
    run_id: str | None = None
    chapter_id: str | None = None
    actor_id: str = "agent"
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    permission: ToolPermission
    handler: Callable[[ToolContext, BaseModel], Any]
    # Upper bound per turn; the budget hook enforces it.
    max_calls_per_turn: int | None = None
    # Approval policies key on this; defaults to the tool name.
    approval_kind: str | None = None

    def parse_arguments(self, arguments: dict[str, Any]) -> BaseModel:
        try:
            return self.input_model.model_validate(arguments or {})
        except ValidationError as exc:
            raise ToolError(f"invalid arguments for {self.name}: {exc.errors(include_url=False)}") from exc

    def provider_schema(self) -> dict[str, Any]:
        """OpenAI-style function tool definition."""
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }


@dataclass(slots=True)
class ToolOutcome:
    ok: bool
    output: Any = None
    error: str | None = None

    def to_content(self) -> dict[str, Any]:
        if self.ok:
            return {"ok": True, "output": _jsonable(self.output)}
        return {"ok": False, "error": self.error}


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class ToolRegistry:
    def __init__(self, tools: Iterable[ToolSpec] = ()) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def provider_schemas(self) -> list[dict[str, Any]]:
        return [self._tools[name].provider_schema() for name in self.names()]

    def __len__(self) -> int:
        return len(self._tools)
