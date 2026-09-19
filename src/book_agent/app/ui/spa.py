"""Serve the built React frontend from the API process (production hosting).

Enabled when ``BOOK_AGENT_FRONTEND_DIST_DIR`` points at a Vite build
(``frontend/dist``); otherwise the legacy status homepage stays on ``/``.

- ``/assets/*`` are content-hashed bundles: served with a long immutable cache.
- ``/runtime-config.js`` tells the bundle where the API lives at runtime, so
  one image works behind any path prefix or reverse proxy without a rebuild.
- Every other GET that is not an API route falls back to ``index.html`` (no
  cache) so client-side routes such as ``/runs`` or ``/issues`` deep-link.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"


class _CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = IMMUTABLE_CACHE
        return response


def frontend_available(dist_dir: Path | None) -> bool:
    return dist_dir is not None and (dist_dir / "index.html").is_file()


def mount_frontend(app: FastAPI, dist_dir: Path, *, api_prefix: str) -> None:
    dist_dir = dist_dir.resolve()
    index_file = dist_dir / "index.html"
    api_prefix = "/" + api_prefix.strip("/")
    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", _CachedStaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/runtime-config.js", include_in_schema=False)
    def runtime_config() -> Response:
        payload = json.dumps({"apiBaseUrl": api_prefix})
        return Response(
            content=f"window.__BOOK_AGENT_CONFIG__ = {payload};\n",
            media_type="application/javascript",
            headers={"Cache-Control": NO_CACHE},
        )

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request) -> Response:
        if request.url.path == api_prefix or request.url.path.startswith(f"{api_prefix}/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if path:
            candidate = (dist_dir / path).resolve()
            if candidate.is_file() and dist_dir in candidate.parents:
                return FileResponse(candidate)
        return FileResponse(index_file, headers={"Cache-Control": NO_CACHE})
