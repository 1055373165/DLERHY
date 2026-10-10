"""Publish a book's Markdown reading copy to the blog (~/project/blog) as an article draft.

The blog's own API does it: log in (POST /api/auth/login) with the configured account, upload
every picture the Markdown references (POST /api/upload/image) and point its link at the uploaded
URL, then create the article (POST /api/articles) with a title, excerpt and SEO fields made by
rule (``export.blog_metadata``). New articles are drafts; publishing the same edition again
updates that article (PUT /api/articles/{id}) and leaves its draft/published state alone.
Uploaded pictures are remembered by content hash, so a republish uploads only new ones.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from book_agent.core.config import Settings

# The blog takes pictures up to 10 MB in these types (handlers.UploadImage).
_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
_IMAGE_LINK = re.compile(r"(!\[[^\]]*\]\()(?P<src>[^)\s]+)((?:\s+\"[^\"]*\")?\))")
_STATE_KEY = "blog_publish"


class BlogPublishError(Exception):
    """A publish that cannot go on; ``status`` is the HTTP status the API answers with."""

    def __init__(self, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class BlogTarget:
    api_url: str
    email: str
    password: str
    web_url: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> BlogTarget | None:
        if not (settings.blog_api_url and settings.blog_email and settings.blog_password):
            return None
        return cls(
            api_url=settings.blog_api_url.rstrip("/"),
            email=settings.blog_email,
            password=settings.blog_password,
            web_url=(settings.blog_web_url or "").rstrip("/") or None,
        )

    def edit_url(self, article_id: int) -> str | None:
        return f"{self.web_url}/admin/articles/{article_id}/edit" if self.web_url else None


@dataclass(slots=True)
class PublishResult:
    article_id: int
    action: str  # "created" or "updated"
    title: str
    edit_url: str | None
    images_uploaded: int = 0
    images_reused: int = 0
    images_skipped: list[str] = field(default_factory=list)


class BlogClient:
    """The blog's API, logged in as the configured account."""

    def __init__(self, target: BlogTarget, *, transport: httpx.BaseTransport | None = None, timeout: float = 120.0) -> None:
        # trust_env off: a desktop proxy (http_proxy) must not carry calls to a local blog.
        self._client = httpx.Client(base_url=target.api_url, timeout=timeout, transport=transport, trust_env=False)
        self._target = target
        self._token: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BlogClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            return self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise BlogPublishError(f"连不上博客（{self._target.api_url}）：{exc}") from exc

    @staticmethod
    def _message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        return str(body.get("error") or body.get("message") or body)[:200]

    def login(self) -> None:
        response = self._request("POST", "/api/auth/login", json={"email": self._target.email, "password": self._target.password})
        if response.status_code != 200:
            raise BlogPublishError(f"博客登录失败（{response.status_code}）：{self._message(response)}")
        token = ((response.json() or {}).get("data") or {}).get("token")
        if not token:
            raise BlogPublishError("博客登录没有返回 token")
        self._token = token

    def upload_image(self, path: Path) -> str:
        content_type = _IMAGE_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "image/png"
        with path.open("rb") as handle:
            response = self._request("POST", "/api/upload/image", files={"file": (path.name, handle, content_type)})
        if response.status_code != 200:
            raise BlogPublishError(f"图片 {path.name} 上传失败（{response.status_code}）：{self._message(response)}")
        url = ((response.json() or {}).get("data") or {}).get("url")
        if not url:
            raise BlogPublishError(f"图片 {path.name} 上传后博客没有返回地址")
        return str(url)

    def create_article(self, payload: dict[str, Any]) -> int:
        response = self._request("POST", "/api/articles", json=payload)
        if response.status_code not in (200, 201):
            raise BlogPublishError(f"博客新建文章失败（{response.status_code}）：{self._message(response)}")
        return int(((response.json() or {}).get("data") or {})["id"])

    def update_article(self, article_id: int, payload: dict[str, Any]) -> bool:
        """False when the article is gone (deleted on the blog)."""
        response = self._request("PUT", f"/api/articles/{article_id}", json=payload)
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise BlogPublishError(f"博客更新文章失败（{response.status_code}）：{self._message(response)}")
        return True


def _local_image_path(src: str, base_dir: Path) -> Path | None:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", src) or src.startswith(("/", "#")):
        return None  # already a web address, or not a file
    from urllib.parse import unquote

    root = base_dir.resolve()
    path = (root / unquote(src.split("?", 1)[0])).resolve()
    return path if root in path.parents and path.is_file() else None


def publish_markdown(
    *,
    target: BlogTarget,
    state: dict[str, Any],
    markdown: str,
    base_dir: Path,
    metadata: Any,
    edition: str,
    client_factory: Callable[[BlogTarget], BlogClient] = BlogClient,
    progress: Callable[[str, int, int], None] | None = None,
) -> tuple[PublishResult, dict[str, Any]]:
    """Upload the pictures, then create or update the article. Returns the result and the new state.

    ``state``: what earlier publishes to this blog left (``document.metadata_json["blog_publish"]``).
    ``progress(stage, done, total)``: "login", then "images" after each picture, then "saving".
    """
    report = progress or (lambda stage, done, total: None)
    state = {key: dict(value) if isinstance(value, dict) else value for key, value in (state or {}).items()}
    blog_state = dict(state.get(target.api_url) or {})
    images: dict[str, str] = dict(blog_state.get("images") or {})
    articles: dict[str, Any] = dict(blog_state.get("articles") or {})
    result = PublishResult(article_id=0, action="", title=metadata.title, edit_url=None)

    # Every picture file the Markdown links to, once.
    pictures: dict[str, Path] = {}
    for match in _IMAGE_LINK.finditer(markdown):
        src = match.group("src")
        path = pictures.get(src) or _local_image_path(src, base_dir)
        if path is not None:
            pictures[src] = path

    with client_factory(target) as client:
        report("login", 0, len(pictures))
        client.login()
        uploaded_by_src: dict[str, str] = {}
        report("images", 0, len(pictures))
        for done, (src, path) in enumerate(pictures.items(), start=1):
            if path.stat().st_size > _IMAGE_MAX_BYTES or path.suffix.lower() not in _IMAGE_TYPES:
                result.images_skipped.append(src)
            else:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest in images:
                    result.images_reused += 1
                else:
                    images[digest] = client.upload_image(path)
                    result.images_uploaded += 1
                uploaded_by_src[src] = images[digest]
            report("images", done, len(pictures))
        report("saving", len(pictures), len(pictures))
        content = _IMAGE_LINK.sub(
            lambda m: f"{m.group(1)}{uploaded_by_src.get(m.group('src'), m.group('src'))}{m.group(3)}", markdown
        )

        payload = {
            "title": metadata.title,
            "content": content,
            "excerpt": metadata.excerpt,
            "meta_title": metadata.meta_title,
            "meta_description": metadata.meta_description,
            "meta_keywords": metadata.meta_keywords,
        }
        previous = (articles.get(edition) or {}).get("id") if isinstance(articles.get(edition), dict) else None
        if previous and client.update_article(int(previous), payload):
            result.article_id, result.action = int(previous), "updated"
        else:
            result.article_id = client.create_article({**payload, "is_published": False})
            result.action = "created"

    articles[edition] = {"id": result.article_id, "published_at": datetime.now(timezone.utc).isoformat()}
    state[target.api_url] = {"images": images, "articles": articles}
    result.edit_url = target.edit_url(result.article_id)
    return result, state


def glossary_terms(session: Any, document_id: str) -> Sequence[str]:
    from sqlalchemy import select

    from book_agent.domain.enums import TermStatus
    from book_agent.domain.models.translation import TermEntry

    return list(
        session.scalars(
            select(TermEntry.target_term).where(TermEntry.document_id == document_id, TermEntry.status == TermStatus.ACTIVE)
        )
    )


STATE_KEY = _STATE_KEY
