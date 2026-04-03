from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from book_agent.domain.models.parse_revision import DocumentParseRevision, DocumentParseRevisionArtifact


@dataclass(slots=True)
class ParseRevisionBundle:
    revision: DocumentParseRevision
    artifacts: list[DocumentParseRevisionArtifact]


class ParseIrRepository:
    def __init__(self, session: Session):
        self.session = session

    def _table_available(self) -> bool:
        connection = self.session.connection()
        inspector = inspect(connection)
        return inspector.has_table(DocumentParseRevision.__tablename__) and inspector.has_table(
            DocumentParseRevisionArtifact.__tablename__
        )

    def save(self, revision: DocumentParseRevision, artifact: DocumentParseRevisionArtifact | None = None) -> None:
        if not self._table_available():
            return
        self.session.merge(revision)
        if artifact is not None:
            self.session.merge(artifact)
        self.session.flush()

    def load_latest(self, document_id: str) -> ParseRevisionBundle | None:
        if not self._table_available():
            return None
        revision = self.session.scalars(
            select(DocumentParseRevision)
            .where(DocumentParseRevision.document_id == document_id)
            .order_by(DocumentParseRevision.version.desc(), DocumentParseRevision.created_at.desc())
        ).first()
        if revision is None:
            return None
        artifacts = self.session.scalars(
            select(DocumentParseRevisionArtifact)
            .where(DocumentParseRevisionArtifact.document_parse_revision_id == revision.id)
            .order_by(DocumentParseRevisionArtifact.created_at.asc(), DocumentParseRevisionArtifact.id.asc())
        ).all()
        return ParseRevisionBundle(revision=revision, artifacts=artifacts)

    def load_revision(self, revision_id: str) -> ParseRevisionBundle | None:
        if not self._table_available():
            return None
        revision = self.session.get(DocumentParseRevision, revision_id)
        if revision is None:
            return None
        artifacts = self.session.scalars(
            select(DocumentParseRevisionArtifact)
            .where(DocumentParseRevisionArtifact.document_parse_revision_id == revision.id)
            .order_by(DocumentParseRevisionArtifact.created_at.asc(), DocumentParseRevisionArtifact.id.asc())
        ).all()
        return ParseRevisionBundle(revision=revision, artifacts=artifacts)
