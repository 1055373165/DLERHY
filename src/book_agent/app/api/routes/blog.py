"""Publishing a reading copy to the blog (``services.blog_publish``)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session
from book_agent.app.api.export_downloads import markdown_export_file
from book_agent.core.config import get_settings
from book_agent.domain.document_titles import display_author_value, document_display_title, document_source_title
from book_agent.export.blog_metadata import build_blog_metadata
from book_agent.services.blog_publish import STATE_KEY, BlogClient, BlogPublishError, BlogTarget, glossary_terms, publish_markdown

documents_router = APIRouter()
target_router = APIRouter()


class BlogPublishRequest(BaseModel):
    # "zh": the Chinese Markdown; "bilingual": the Chinese–English one.
    edition: Literal["zh", "bilingual"] = "zh"


class BlogPublishResponse(BaseModel):
    article_id: int
    action: Literal["created", "updated"]
    title: str
    edit_url: str | None = None
    images_uploaded: int
    images_reused: int
    images_skipped: list[str]


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


@documents_router.post("/{document_id}/blog-publish", response_model=BlogPublishResponse)
def publish_to_blog(
    document_id: str,
    payload: BlogPublishRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> BlogPublishResponse:
    """Save the book's Markdown reading copy as a blog article draft (an earlier one is updated).

    404 when the Markdown export is missing or out of date: export it first, as for a download.
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
        document = export_repository.get_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    bilingual = payload.edition == "bilingual"
    markdown_path = markdown_export_file(
        export_repository, document_id, artifact_roots=_artifact_roots(request), bilingual=bilingual
    )
    markdown = markdown_path.read_text(encoding="utf-8")
    metadata = build_blog_metadata(
        markdown=markdown,
        title=document_display_title(document) or document_id,
        source_title=document_source_title(document),
        authors=display_author_value(document.author),
        glossary_terms=glossary_terms(session, document_id),
        bilingual=bilingual,
    )
    try:
        result, state = publish_markdown(
            target=target,
            state=(document.metadata_json or {}).get(STATE_KEY) or {},
            markdown=markdown,
            base_dir=markdown_path.parent,
            metadata=metadata,
            edition=payload.edition,
            client_factory=BlogClient,
        )
    except BlogPublishError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    document.metadata_json = {**(document.metadata_json or {}), STATE_KEY: state}
    session.commit()
    return BlogPublishResponse(
        article_id=result.article_id,
        action=result.action,
        title=result.title,
        edit_url=result.edit_url,
        images_uploaded=result.images_uploaded,
        images_reused=result.images_reused,
        images_skipped=result.images_skipped,
    )
