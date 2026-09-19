"""BOOK.md: the book-level guide rendered from decisions and the glossary.

It is the agent-and-human-written counterpart of AGENTS.md: register, genre,
preservation policy and resolved ambiguities, plus the locked glossary. The
translation prompt carries the decisions part as a book-level system message
(cache-friendly: identical for every packet of the book); the glossary keeps
flowing through the per-packet relevance filter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from book_agent.domain.enums import DecisionScope, LockLevel
from book_agent.domain.models.agent import Decision
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.services.glossary_service import GlossaryService

# Decision keys the Terminology agent and humans use; free-form keys are
# rendered under "其他决议".
KEY_GENRE = "book.genre"
KEY_REGISTER = "book.register"
KEY_AUDIENCE = "book.audience"
KEY_PRESERVATION = "book.preservation_policy"
KEY_STYLE_NOTES = "book.style_notes"
_TITLED_KEYS = {
    KEY_GENRE: "体裁",
    KEY_REGISTER: "语域与文风",
    KEY_AUDIENCE: "目标读者",
    KEY_PRESERVATION: "保留策略",
    KEY_STYLE_NOTES: "风格备注",
}


@dataclass(slots=True)
class BookGuide:
    decisions: dict[str, Decision] = field(default_factory=dict)
    locked_terms: list[tuple[str, str]] = field(default_factory=list)
    preferred_terms: list[tuple[str, str]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.decisions and not self.locked_terms and not self.preferred_terms

    def render_markdown(self, *, title: str | None = None) -> str:
        lines: list[str] = [f"# BOOK.md — {title}" if title else "# BOOK.md", ""]
        for key, heading in _TITLED_KEYS.items():
            decision = self.decisions.get(key)
            if decision is None:
                continue
            lines.append(f"## {heading}")
            lines.append(_decision_text(decision))
            lines.append("")
        others = [d for key, d in self.decisions.items() if key not in _TITLED_KEYS and not key.startswith("term:")]
        if others:
            lines.append("## 其他决议")
            for decision in others:
                lines.append(f"- {decision.key}: {_decision_text(decision)}")
            lines.append("")
        if self.locked_terms or self.preferred_terms:
            lines.append("## 术语表")
            for source, target in self.locked_terms:
                lines.append(f"- {source} => {target} (locked)")
            for source, target in self.preferred_terms:
                lines.append(f"- {source} => {target} (preferred)")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def render_prompt_guidance(self) -> str:
        """The decisions part only, as a compact prompt segment (glossary comes separately)."""
        lines: list[str] = []
        for key, heading in _TITLED_KEYS.items():
            decision = self.decisions.get(key)
            if decision is None:
                continue
            lines.append(f"- {heading}: {_decision_text(decision)}")
        for key, decision in self.decisions.items():
            if key in _TITLED_KEYS or key.startswith("term:"):
                continue
            lines.append(f"- {key}: {_decision_text(decision)}")
        if not lines:
            return ""
        return "Book Guidance:\n" + "\n".join(lines)


def _decision_text(decision: Decision) -> str:
    value = decision.value_json or {}
    text = value.get("text")
    if isinstance(text, str) and text.strip():
        body = text.strip()
    else:
        body = ", ".join(f"{k}={v}" for k, v in value.items()) if value else ""
    if decision.rationale:
        return f"{body}（{decision.rationale.strip()}）" if body else decision.rationale.strip()
    return body


def load_book_guide(session: Session, document_id: str) -> BookGuide:
    ledger = AgentLedgerRepository(session)
    decisions = ledger.latest_decisions(document_id, scope=DecisionScope.BOOK)
    guide = BookGuide(decisions=decisions)
    for entry in GlossaryService(session).list_document_entries(document_id):
        if not entry.target_term or not entry.target_term.strip():
            continue
        pair = (entry.source_term, entry.target_term)
        if entry.lock_level == LockLevel.LOCKED:
            guide.locked_terms.append(pair)
        elif entry.lock_level == LockLevel.PREFERRED:
            guide.preferred_terms.append(pair)
    guide.locked_terms.sort(key=lambda pair: pair[0].casefold())
    guide.preferred_terms.sort(key=lambda pair: pair[0].casefold())
    return guide


def book_prompt_guidance(session: Session, document_id: str) -> str:
    return load_book_guide(session, document_id).render_prompt_guidance()
