"""API authentication, roles and organisation scoping (R1).

With ``auth_mode=api_key`` every API route except ``/health`` and ``/meta``
depends on ``enforce_access``:

- with ``oidc_issuer`` set, a bearer JWT from that issuer works too
  (``services/oidc.py``: org and role come from token claims);
- the key comes from ``Authorization: Bearer <key>`` or ``X-API-Key``; the
  run event stream also accepts ``?access_token=`` because EventSource
  cannot send headers;
- roles: ``viewer`` may read, ``editor`` may also write, ``admin`` may also
  manage provider credentials and API keys;
- every document, run, issue, approval, action and export named in the path
  must belong to the key's organisation. Anything else answers 404 so other
  organisations' ids do not leak.

With auth disabled (development, tests) the caller is an admin of the
default organisation. The principal is on ``request.state.principal`` for
handlers that create or list documents.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session
from book_agent.core.config import get_settings
from book_agent.domain.models import Document
from book_agent.domain.models.agent import Approval
from book_agent.domain.models.auth import DEFAULT_ORG_ID
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.review import Export, IssueAction, ReviewIssue
from book_agent.services.api_keys import ApiKeyService

_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}
_ADMIN_PATH_PREFIXES = ("/providers", "/api-keys")
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True, slots=True)
class Principal:
    org_id: str
    role: str
    key_id: str | None = None
    auth_enabled: bool = True
    # OIDC subject when the caller presented a token instead of an API key.
    subject: str | None = None

    def allows(self, role: str) -> bool:
        return _ROLE_RANK[self.role] >= _ROLE_RANK[role]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _presented_key(request: Request) -> str | None:
    authorization = request.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if request.headers.get("x-api-key"):
        return request.headers["x-api-key"].strip()
    if request.method == "GET" and request.url.path.endswith("/stream"):
        return request.query_params.get("access_token")
    return None


def _api_path(request: Request) -> str:
    prefix = "/" + get_settings().api_prefix.strip("/")
    path = request.url.path
    return path[len(prefix):] if path.startswith(prefix) else path


def required_role(request: Request) -> str:
    path = _api_path(request)
    if path.startswith(_ADMIN_PATH_PREFIXES):
        return "admin"
    if path.startswith("/orgs") and request.method.upper() not in _READ_METHODS:
        return "admin"
    return "viewer" if request.method.upper() in _READ_METHODS else "editor"


def _document_org(session: Session, request: Request) -> list[str | None]:
    """Organisation of every resource named in the path (None: the resource does not exist)."""
    params = request.path_params
    orgs: list[str | None] = []

    def document_org(document_id: str | None) -> str | None:
        document = session.get(Document, document_id) if document_id else None
        return document.org_id if document is not None else None

    try:
        if "document_id" in params:
            orgs.append(document_org(params["document_id"]))
        if "run_id" in params:
            run = session.get(DocumentRun, params["run_id"])
            orgs.append(document_org(run.document_id) if run is not None else None)
        if "issue_id" in params:
            issue = session.get(ReviewIssue, params["issue_id"])
            orgs.append(document_org(issue.document_id) if issue is not None else None)
        if "approval_id" in params:
            approval = session.get(Approval, params["approval_id"])
            orgs.append(document_org(approval.document_id) if approval is not None else None)
        if "action_id" in params:
            action = session.get(IssueAction, params["action_id"])
            issue = session.get(ReviewIssue, action.issue_id) if action is not None else None
            orgs.append(document_org(issue.document_id) if issue is not None else None)
        if "export_id" in params:
            export = session.get(Export, params["export_id"])
            orgs.append(document_org(export.document_id) if export is not None else None)
    except (ValueError, TypeError):
        # Malformed ids (not UUIDs) cannot name another org's resource; let the route answer.
        return []
    return orgs


def _authenticate(session: Session, settings, presented: str) -> Principal:
    from book_agent.services.oidc import OidcError, looks_like_jwt, verifier_for

    verifier = verifier_for(settings)
    if verifier is not None and looks_like_jwt(presented):
        try:
            identity = verifier.verify(session, presented)
        except OidcError as exc:
            raise _unauthorized(str(exc)) from exc
        return Principal(org_id=identity.org_id, role=identity.role, subject=identity.subject)
    key = ApiKeyService(session).authenticate(presented)
    if key is None:
        raise _unauthorized("invalid or revoked API key")
    return Principal(org_id=key.org_id, role=key.role, key_id=key.id)


def enforce_access(request: Request, session: Session = Depends(get_db_session)) -> Principal:
    settings = get_settings()
    if not settings.auth_enabled:
        principal = Principal(org_id=DEFAULT_ORG_ID, role="admin", auth_enabled=False)
        request.state.principal = principal
        return principal
    presented = _presented_key(request)
    if not presented:
        raise _unauthorized("API key required")
    principal = _authenticate(session, settings, presented)
    needed = required_role(request)
    if not principal.allows(needed):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"this action needs the {needed} role")
    for org_id in _document_org(session, request):
        # Missing resources fall through to the route's own 404.
        if org_id is not None and org_id != principal.org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    request.state.principal = principal
    return principal


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return Principal(org_id=DEFAULT_ORG_ID, role="admin", auth_enabled=False)
    return principal


def require_document_in_org(session: Session, request: Request, document_id: str) -> None:
    """For ids that arrive in a request body rather than the path."""
    principal = current_principal(request)
    if not principal.auth_enabled:
        return
    document = session.get(Document, document_id)
    if document is not None and document.org_id != principal.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
