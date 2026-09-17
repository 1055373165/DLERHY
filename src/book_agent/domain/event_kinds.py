"""Frozen catalog of event kinds (Phase 4 CC-2).

Every value ever stored in ``events.kind`` must appear in :data:`EVENT_KINDS`.
New kinds are added by PR that also updates the frontend TypeScript union
and any relevant consumers. ``emit_event`` rejects unknown kinds at runtime
to keep the catalog truthful.

Naming: ``<domain>.<subject>.<verb>`` in lowercase dot-separated form.
"""

from __future__ import annotations

from typing import Final


# --- Run lifecycle -----------------------------------------------------------
RUN_CREATED: Final = "run.created"
RUN_PAUSED: Final = "run.paused"
RUN_RESUMED: Final = "run.resumed"
RUN_CANCELLED: Final = "run.cancelled"
RUN_COMPLETED: Final = "run.completed"

# --- Chapter progress --------------------------------------------------------
CHAPTER_STARTED: Final = "chapter.started"
CHAPTER_COMPLETED: Final = "chapter.completed"

# --- Packet lifecycle --------------------------------------------------------
PACKET_BUILT: Final = "packet.built"
PACKET_LEASED: Final = "packet.leased"
PACKET_TRANSLATED: Final = "packet.translated"
PACKET_FAILED: Final = "packet.failed"

# --- LLM calls ---------------------------------------------------------------
LLM_CALL_STARTED: Final = "llm.call.started"
LLM_CALL_COMPLETED: Final = "llm.call.completed"
LLM_CALL_FAILED: Final = "llm.call.failed"

# --- Glossary / terminology --------------------------------------------------
GLOSSARY_INJECTED: Final = "glossary.injected"
GLOSSARY_RESOLVED: Final = "glossary.resolved"
GLOSSARY_VIOLATION: Final = "glossary.violation"
GLOSSARY_SUPPRESSED: Final = "glossary.suppressed"
GLOSSARY_UPDATED: Final = "glossary.updated"

# --- Review / actions --------------------------------------------------------
REVIEW_ISSUE_OPENED: Final = "review.issue.opened"
REVIEW_ISSUE_CLOSED: Final = "review.issue.closed"
ACTION_DISPATCHED: Final = "action.dispatched"

# --- Cost / budget -----------------------------------------------------------
COST_BUDGET_WARNING: Final = "cost.budget.warning"
COST_BUDGET_EXCEEDED: Final = "cost.budget.exceeded"

# --- Agent runtime (Phase 8) -------------------------------------------------
AGENT_TRACE_STARTED: Final = "agent.trace.started"
AGENT_TRACE_COMPLETED: Final = "agent.trace.completed"
AGENT_HELP_REQUESTED: Final = "agent.help.requested"
AGENT_TURN_STARTED: Final = "agent.turn.started"
AGENT_TURN_FINISHED: Final = "agent.turn.finished"
AGENT_TOOL_CALLED: Final = "agent.tool.called"
AGENT_TOOL_RETURNED: Final = "agent.tool.returned"
AGENT_APPROVAL_REQUESTED: Final = "agent.approval.requested"
AGENT_APPROVAL_DECIDED: Final = "agent.approval.decided"


EVENT_KINDS: frozenset[str] = frozenset(
    {
        RUN_CREATED,
        RUN_PAUSED,
        RUN_RESUMED,
        RUN_CANCELLED,
        RUN_COMPLETED,
        CHAPTER_STARTED,
        CHAPTER_COMPLETED,
        PACKET_BUILT,
        PACKET_LEASED,
        PACKET_TRANSLATED,
        PACKET_FAILED,
        LLM_CALL_STARTED,
        LLM_CALL_COMPLETED,
        LLM_CALL_FAILED,
        GLOSSARY_INJECTED,
        GLOSSARY_RESOLVED,
        GLOSSARY_VIOLATION,
        GLOSSARY_SUPPRESSED,
        GLOSSARY_UPDATED,
        REVIEW_ISSUE_OPENED,
        REVIEW_ISSUE_CLOSED,
        ACTION_DISPATCHED,
        COST_BUDGET_WARNING,
        COST_BUDGET_EXCEEDED,
        AGENT_TRACE_STARTED,
        AGENT_TRACE_COMPLETED,
        AGENT_HELP_REQUESTED,
        AGENT_TURN_STARTED,
        AGENT_TURN_FINISHED,
        AGENT_TOOL_CALLED,
        AGENT_TOOL_RETURNED,
        AGENT_APPROVAL_REQUESTED,
        AGENT_APPROVAL_DECIDED,
    }
)


VALID_ACTOR_KINDS: frozenset[str] = frozenset({"user", "agent", "system"})


def is_valid_kind(kind: str) -> bool:
    return kind in EVENT_KINDS
