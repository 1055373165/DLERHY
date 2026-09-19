"""GET /metrics (Prometheus) and the HTTP request metrics middleware.

Access: with BOOK_AGENT_METRICS_TOKEN set, the scraper sends it as a bearer
token; otherwise, with API keys on, an admin key is required; in
development the endpoint is open.
"""

from __future__ import annotations

import hmac
import time

from fastapi import FastAPI, HTTPException, Request, Response
from sqlalchemy import func, select

from book_agent.core.config import get_settings
from book_agent.domain.models.ops import DocumentRun, WorkItem
from book_agent.infra import metrics, tracing


def install_metrics(app: FastAPI) -> None:
    @app.middleware("http")
    async def http_metrics(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        with tracing.span(
            f"HTTP {request.method}",
            parent_headers=request.headers,
            **{"http.request.method": request.method, "url.path": request.url.path},
        ) as current:
            try:
                response = await call_next(request)
                status = response.status_code
                return response
            finally:
                route = request.scope.get("route")
                template = getattr(route, "path", None) or "unmatched"
                if current is not None:
                    current.update_name(f"{request.method} {template}")
                    tracing.set_attributes(current, **{"http.route": template, "http.response.status_code": status})
                _observe(request.method, template, status, started)

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics(request: Request) -> Response:
        _authorize(request)
        lines = metrics.render_registry()
        lines.extend(_scrape_time_gauges(request))
        return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")


def _observe(method: str, template: str, status: int, started: float) -> None:
    if template != "/metrics":
        metrics.HTTP_REQUESTS.inc(method=method, route=template, status=str(status))
        metrics.HTTP_DURATION.observe(time.perf_counter() - started, method=method, route=template)


def _authorize(request: Request) -> None:
    settings = get_settings()
    presented = (request.headers.get("authorization") or "")
    token = presented[7:].strip() if presented.lower().startswith("bearer ") else ""
    if settings.metrics_token:
        if not token or not hmac.compare_digest(token, settings.metrics_token):
            raise HTTPException(status_code=401, detail="metrics token required")
        return
    if not settings.auth_enabled:
        return
    from book_agent.infra.db.session import session_scope
    from book_agent.services.api_keys import ApiKeyService

    factory = request.app.state.session_factory
    with session_scope(factory, commit_on_exit=False) as session:
        key = ApiKeyService(session).authenticate(token)
        if key is None or key.role != "admin":
            raise HTTPException(status_code=401, detail="admin API key or metrics token required")


def _scrape_time_gauges(request: Request) -> list[str]:
    lines: list[str] = []
    factory = getattr(request.app.state, "session_factory", None)
    engine = factory.kw.get("bind") if factory is not None else None
    pool = getattr(engine, "pool", None)
    if pool is not None and hasattr(pool, "checkedout"):
        samples = [({"state": "checked_out"}, float(pool.checkedout()))]
        if hasattr(pool, "size"):
            samples.append(({"state": "size"}, float(pool.size())))
        if hasattr(pool, "overflow"):
            samples.append(({"state": "overflow"}, float(pool.overflow())))
        lines.extend(metrics.render_gauge("book_agent_db_pool_connections", "Database connection pool usage.", samples))
    if factory is None:
        return lines
    try:
        with factory() as session:
            runs = session.execute(select(DocumentRun.status, func.count()).group_by(DocumentRun.status)).all()
            items = session.execute(
                select(WorkItem.stage, WorkItem.status, func.count())
                .where(WorkItem.status.in_(["pending", "leased", "running", "retryable_failed"]))
                .group_by(WorkItem.stage, WorkItem.status)
            ).all()
    except Exception:  # a metrics scrape must not fail because the database is down
        return lines + metrics.render_gauge("book_agent_metrics_database_up", "Whether the scrape could query the database.", [({}, 0.0)])
    lines.extend(
        metrics.render_gauge(
            "book_agent_runs", "Document runs by status.", [({"status": str(getattr(status, "value", status))}, float(count)) for status, count in runs]
        )
    )
    lines.extend(
        metrics.render_gauge(
            "book_agent_work_items_open",
            "Work items not yet finished, by stage and status.",
            [({"stage": str(getattr(stage, "value", stage)), "status": str(getattr(status, "value", status))}, float(count)) for stage, status, count in items],
        )
    )
    lines.extend(metrics.render_gauge("book_agent_metrics_database_up", "Whether the scrape could query the database.", [({}, 1.0)]))
    return lines
