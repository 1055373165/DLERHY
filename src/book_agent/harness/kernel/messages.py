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


# A tool may return images under this key of its output dict (e.g. a rendered
# page); they are stored on the tool_result item and sent as an image message.
IMAGE_OUTPUT_KEY = "_images"
# Rough provider cost of one page-sized image, for budgets and compaction.
IMAGE_TOKEN_ESTIMATE = 1_000


def _image_message(item_name: str, images: list[dict[str, Any]]) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"type": "text", "text": f"Image(s) returned by {item_name}:"}]
    for image in images:
        media_type = str(image.get("media_type") or "image/png")
        parts.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image.get('data', '')}"}})
    return {"role": "user", "content": parts}


def assemble_messages(items: list[AgentItem]) -> list[dict[str, Any]]:
    system_messages: list[dict[str, Any]] = []
    conversation: list[dict[str, Any]] = []
    compaction: dict[str, Any] | None = None
    # Chat APIs need every tool message right after its assistant message, so
    # image messages wait until the run of tool messages ends.
    pending_images: list[dict[str, Any]] = []

    def flush_images() -> None:
        conversation.extend(pending_images)
        pending_images.clear()

    for item in items:
        content = item.content_json or {}
        if item.kind != AgentItemKind.TOOL_RESULT and item.kind not in (AgentItemKind.TOOL_CALL, AgentItemKind.APPROVAL_REQUEST, AgentItemKind.APPROVAL_RESULT):
            flush_images()
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
            if content.get("images"):
                pending_images.append(_image_message(str(content.get("name") or "tool"), list(content["images"])))
        # tool_call / approval_request / approval_result items are bookkeeping;
        # the model only ever sees the resulting tool_result.
    flush_images()
    conversation = _order_tool_messages(conversation)
    messages = list(system_messages)
    if compaction is not None:
        messages.append(compaction)
    messages.extend(conversation)
    return messages


def _order_tool_messages(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool messages after an assistant message in the order of its tool calls.

    Calls that waited for approval finish after later calls of the same step,
    so ledger order is not call order.
    """
    ordered: list[dict[str, Any]] = []
    index = 0
    while index < len(conversation):
        message = conversation[index]
        ordered.append(message)
        index += 1
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        run_end = index
        while run_end < len(conversation) and conversation[run_end].get("role") == "tool":
            run_end += 1
        position = {call["id"]: order for order, call in enumerate(message["tool_calls"])}
        ordered.extend(
            sorted(conversation[index:run_end], key=lambda tool: position.get(tool.get("tool_call_id"), len(position)))
        )
        index = run_end
    return ordered


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
        if item.token_count:
            total += item.token_count
            continue
        content = {k: v for k, v in (item.content_json or {}).items() if k != "images"}
        total += estimate_tokens(json.dumps(content, ensure_ascii=False)) + IMAGE_TOKEN_ESTIMATE * len((item.content_json or {}).get("images") or [])
    return total
