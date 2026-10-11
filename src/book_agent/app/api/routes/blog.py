"""Publishing a reading copy to the blog (``services.blog_publish``).

A publish runs in the background (a book's first one uploads hundreds of pictures): the POST answers
202 with a job, and GET /blog/publish-jobs/{id} reports its progress until it ends. Jobs live in
this process's memory; one publish of a book's edition runs at a time.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session, get_session_factory
from book_agent.app.api.export_downloads import markdown_export_file
from book_agent.core.config import get_settings
from book_agent.domain.document_titles import display_author_value, document_display_title, document_source_title
from book_agent.export.blog_metadata import build_blog_metadata
from book_agent.services.blog_publish import STATE_KEY, BlogClient, BlogPublishError, BlogTarget, glossary_terms, publish_markdown
from book_agent.services.book_cover import book_cover

documents_router = APIRouter()
target_router = APIRouter()


class BlogPublishRequest(BaseModel):
    # "zh": the Chinese Markdown; "bilingual": the Chinese–English one.
    edition: Literal["zh", "bilingual"] = "zh"


class BlogPublishResponse(BaseModel):
    article_id: int
    # "unchanged": the article's body was already this one; nothing was sent.
    action: Literal["created", "updated", "unchanged"]
    title: str
    edit_url: str | None = None
    images_uploaded: int
    images_reused: int
    images_skipped: list[str]
    # "uploaded"/"reused": set as the article's cover; "kept": it had one; "none": no cover found.
    cover: Literal["uploaded", "reused", "kept", "none"] = "none"


class BlogPublishJobResponse(BaseModel):
    job_id: str
    document_id: str
    edition: Literal["zh", "bilingual"]
    status: Literal["running", "succeeded", "failed"]
    # "queued", "login", "images" (done/total pictures), "saving", "done".
    stage: str
    done: int
    total: int
    result: BlogPublishResponse | None = None
    error: str | None = None


@dataclass
class _Job:
    job_id: str
    document_id: str
    edition: str
    status: str = "running"
    stage: str = "queued"
    done: int = 0
    total: int = 0
    result: BlogPublishResponse | None = None
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def response(self) -> BlogPublishJobResponse:
        return BlogPublishJobResponse(
            job_id=self.job_id,
            document_id=self.document_id,
            edition=self.edition,  # type: ignore[arg-type]
            status=self.status,  # type: ignore[arg-type]
            stage=self.stage,
            done=self.done,
            total=self.total,
            result=self.result,
            error=self.error,
        )


_JOBS: dict[str, _Job] = {}
_JOBS_LOCK = threading.Lock()
_JOBS_KEPT = 50


def _remember(job: _Job) -> None:
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
        for old in sorted(_JOBS.values(), key=lambda item: item.started_at)[: max(0, len(_JOBS) - _JOBS_KEPT)]:
            if old.status != "running":
                _JOBS.pop(old.job_id, None)


def _running_job(document_id: str, edition: str) -> _Job | None:
    with _JOBS_LOCK:
        return next(
            (job for job in _JOBS.values() if job.status == "running" and job.document_id == document_id and job.edition == edition),
            None,
        )


@target_router.get("/publish-jobs/{job_id}", response_model=BlogPublishJobResponse)
def blog_publish_job(job_id: str) -> BlogPublishJobResponse:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such publish job (the server may have restarted).")
    return job.response()


class BlogTargetResponse(BaseModel):
    configured: bool
    api_url: str | None = None
    web_url: str | None = None


@target_router.get("/target", response_model=BlogTargetResponse)
def blog_target() -> BlogTargetResponse:
    """Whether a blog is configured (the 发布到博客 action is offered only then). No secrets."""
    target = BlogTarget.from_settings(get_settings())
    if target is None:
        return BlogTargetResponse(configured=False)
    return BlogTargetResponse(configured=True, api_url=target.api_url, web_url=target.web_url)


@documents_router.post(
    "/{document_id}/blog-publish", response_model=BlogPublishJobResponse, status_code=status.HTTP_202_ACCEPTED
)
def publish_to_blog(
    document_id: str,
    payload: BlogPublishRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> BlogPublishJobResponse:
    """Start saving the book's Markdown reading copy as a blog article draft (an earlier one is updated).

    422 when no blog is configured; 404 when the Markdown export is missing or out of date (export it
    first, as for a download). Otherwise 202 with the job; a publish of this edition already running
    is answered with that job.
    """
    from book_agent.app.api.routes.documents import _artifact_roots, _workflow_service

    target = BlogTarget.from_settings(get_settings())
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="还没有配置博客：在 .env 里设置 BOOK_AGENT_BLOG_API_URL、BOOK_AGENT_BLOG_EMAIL、BOOK_AGENT_BLOG_PASSWORD。",
        )
    export_repository = _workflow_service(request, session).export_repository
    try:
        export_repository.get_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    running = _running_job(document_id, payload.edition)
    if running is not None:
        return running.response()
    markdown_path = markdown_export_file(
        export_repository, document_id, artifact_roots=_artifact_roots(request), bilingual=payload.edition == "bilingual"
    )
    job = _Job(job_id=uuid.uuid4().hex, document_id=document_id, edition=payload.edition)
    _remember(job)
    session_factory = get_session_factory(request)
    threading.Thread(
        target=_run_publish,
        args=(job, target, markdown_path, session_factory),
        name=f"blog-publish-{job.job_id[:8]}",
        daemon=True,
    ).start()
    return job.response()


def _run_publish(job: _Job, target: BlogTarget, markdown_path: Path, session_factory: Any) -> None:
    def progress(stage: str, done: int, total: int) -> None:
        job.stage, job.done, job.total = stage, done, total

    bilingual = job.edition == "bilingual"
    try:
        with session_factory() as session:
            from book_agent.domain.models import Document

            document = session.get(Document, job.document_id)
            if document is None:
                raise BlogPublishError("这本书已经不存在了", status=404)
            markdown = markdown_path.read_text(encoding="utf-8")
            metadata = build_blog_metadata(
                markdown=markdown,
                title=document_display_title(document) or job.document_id,
                source_title=document_source_title(document),
                authors=display_author_value(document.author),
                glossary_terms=glossary_terms(session, job.document_id),
                bilingual=bilingual,
            )
            source_path = document.source_path
            try:
                result, state = publish_markdown(
                    target=target,
                    state=(document.metadata_json or {}).get(STATE_KEY) or {},
                    markdown=markdown,
                    base_dir=markdown_path.parent,
                    metadata=metadata,
                    edition=job.edition,
                    cover=lambda: book_cover(source_path),
                    client_factory=BlogClient,
                    progress=progress,
                )
            except BlogPublishError as exc:
                # Pictures uploaded before the failure are remembered, so a retry does not upload them again.
                if exc.state is not None:
                    document.metadata_json = {**(document.metadata_json or {}), STATE_KEY: exc.state}
                    session.commit()
                raise
            document.metadata_json = {**(document.metadata_json or {}), STATE_KEY: state}
            session.commit()
        job.result = BlogPublishResponse(
            article_id=result.article_id,
            action=result.action,  # type: ignore[arg-type]
            title=result.title,
            edit_url=result.edit_url,
            images_uploaded=result.images_uploaded,
            images_reused=result.images_reused,
            images_skipped=result.images_skipped,
            cover=result.cover,  # type: ignore[arg-type]
        )
        job.stage, job.status = "done", "succeeded"
    except BlogPublishError as exc:
        job.error, job.status = exc.message, "failed"
    except Exception as exc:  # noqa: BLE001 - the job reports any failure instead of dying silently
        job.error, job.status = f"发布失败：{exc}", "failed"
