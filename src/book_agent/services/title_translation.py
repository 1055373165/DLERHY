"""Translate the book title once, before exports, when the source gave no Chinese title.

EPUB books usually carry a translated title in their front matter, which the
export derives; PDF books do not, so their exports kept the English title.
One small structured call to the configured provider fixes that. The book's
locked glossary goes into the prompt so the title uses the same renderings as
the text. Any failure leaves the title as it was: exports never wait on it.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from book_agent.domain.document_titles import document_source_title
from book_agent.domain.models import Document
from book_agent.services.glossary_service import GlossaryService
from book_agent.workers.llm_calls import observed_llm_call

logger = logging.getLogger(__name__)

CALL_KIND_TITLE_TRANSLATE = "title.translate"
RESOLUTION_SOURCE = "model_title_translation"
_CJK = re.compile(r"[一-鿿]")
_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string", "description": "The book title in Simplified Chinese."}},
    "required": ["title"],
}
_SYSTEM_PROMPT = (
    "You translate English book titles into the Simplified Chinese title a Chinese publisher would print. "
    "Keep proper nouns, brand names and abbreviations such as RSI as they are unless the glossary says otherwise. "
    "Keep the title's structure (main title and subtitle separated by a colon). "
    'Reply with JSON {"title": "..."} and nothing else.'
)


def has_cjk(text: str | None) -> bool:
    return bool(text and _CJK.search(text))


class TitleTranslationService:
    def __init__(self, session: Session, client: Any, *, model_name: str) -> None:
        self.session = session
        self.client = client
        self.model_name = model_name

    def ensure_document_title(self, document_id: str) -> str | None:
        """The document's Chinese title, translating it first when it is missing."""
        document = self.session.get(Document, document_id)
        if document is None:
            return None
        if document.title_tgt:
            return document.title_tgt
        source = document_source_title(document)
        if not source or has_cjk(source) or self.client is None:
            return None
        terms = GlossaryService(self.session).locked_terms_for_chapter(document_id, None)
        glossary = "\n".join(f"- {term.source_term} => {term.expected_target_term}" for term in terms[:60])
        user_prompt = f"Title: {source}\n" + (f"Glossary (use these renderings):\n{glossary}\n" if glossary else "")
        try:
            with observed_llm_call(
                self.session,
                call_kind=CALL_KIND_TITLE_TRANSLATE,
                model=self.model_name,
                document_id=document.id,
                payload={"document_id": document.id},
            ) as call:
                payload, usage = self.client.generate_structured_object(
                    model_name=self.model_name,
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    response_schema=_SCHEMA,
                    schema_name="book_title",
                )
                call.complete(usage)
        except Exception:
            logger.warning("Translating the title of document %s failed; exports keep the source title", document_id, exc_info=True)
            return None
        title = " ".join(str((payload or {}).get("title") or "").split())
        if not has_cjk(title) or len(title) > 2 * len(source) + 20:
            logger.warning("Discarded an unusable title translation for document %s: %r", document_id, title[:120])
            return None
        metadata = dict(document.metadata_json or {})
        metadata["document_title_tgt"] = title
        metadata["document_title_tgt_resolution_source"] = RESOLUTION_SOURCE
        document.title_tgt = title
        document.metadata_json = metadata
        self.session.flush()
        return title
