"""Rebuild the provider message list from a turn's ledger.

The ledger is the truth; messages are derived from it on every step so a
turn can resume from any point. Ordering is cache-friendly: system items
first (static persona and contract, then book/chapter guidance), then the
latest compaction summary, then the conversation after it.
"""

from __future__ import annotations

import json
from typing import Any

from book_agent.domain.enums import AgentItemKind
from book_agent.domain.models.agent import AgentItem


def assemble_messages(items: list[AgentItem]) -> list[dict[str, Any]]:
    system_messages: list[dict[str, Any]] = []
    conversation: list[dict[str, Any]] = []
    compaction: dict[str, Any] | None = None
    for item in items:
        content = item.content_json or {}
        if item.kind in (AgentItemKind.SYSTEM, AgentItemKind.DEVELOPER):
            system_messages.append({"role": "system", "content": str(content.get("text", ""))})
        elif item.kind == AgentItemKind.COMPACTION:
            compaction = {"role": "system", "content": "Summary of the work so far:\n" + str(content.get("summary", ""))}
            conversation = []
        elif item.kind == AgentItemKind.USER:
            conversation.append({"role": "user", "content": str(content.get("text", ""))})
        elif item.kind == AgentItemKind.ASSISTANT:
            message: dict[str, Any] = {"role": "assistant", "content": content.get("text") or ""}
            tool_calls = content.get("tool_calls") or []
            if tool_calls:
                message["tool_calls"] = [
                    {
                        "id": call["call_id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False)},
                    }
                    for call in tool_calls
                ]
            conversation.append(message)
        elif item.kind == AgentItemKind.TOOL_RESULT:
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": str(content.get("call_id")),
                    "content": json.dumps(content.get("result") or {}, ensure_ascii=False),
                }
            )
        # tool_call / approval_request / approval_result items are bookkeeping;
        # the model only ever sees the resulting tool_result.
    messages = list(system_messages)
    if compaction is not None:
        messages.append(compaction)
    messages.extend(conversation)
    return messages


def estimate_tokens(text: str) -> int:
    # Rough, provider-independent: CJK ~1 token per char, Latin ~4 chars per token.
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk + max(0, (len(text) - cjk) // 4)


def conversation_size(items: list[AgentItem]) -> int:
    """Estimated tokens of everything after the last compaction (system items excluded)."""
    total = 0
    for item in items:
        if item.kind in (AgentItemKind.SYSTEM, AgentItemKind.DEVELOPER):
            continue
        if item.kind == AgentItemKind.COMPACTION:
            total = 0
            continue
        total += item.token_count or estimate_tokens(json.dumps(item.content_json or {}, ensure_ascii=False))
    return total
