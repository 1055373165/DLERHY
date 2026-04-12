"""End-to-end SSE test for /v1/runs/{id}/stream.

Requires a live Postgres (NOTIFY/LISTEN). Skipped otherwise.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from book_agent.app.api.router import api_router
from book_agent.infra.repositories.events import emit_event


DEFAULT_DEV_DSN = "postgresql+psycopg://postgres:postgres@localhost:55432/book_agent"


def _pg_dsn() -> str | None:
    dsn = os.environ.get("BOOK_AGENT_DATABASE_URL", DEFAULT_DEV_DSN)
    return dsn if dsn.startswith("postgresql") else None


@pytest.fixture(scope="module")
def pg_app() -> Iterator[tuple[FastAPI, str, sessionmaker]]:
    dsn = _pg_dsn()
    if dsn is None:
        pytest.skip("SSE endpoint requires Postgres.")
    engine = create_engine(dsn, future=True)
    try:
        with engine.connect() as conn:
            from sqlalchemy import text as _t
            conn.execute(_t("SELECT 1 FROM events LIMIT 0"))
    except Exception as exc:
        pytest.skip(f"events table missing: {exc}")

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    app = FastAPI()
    app.state.session_factory = factory
    app.include_router(api_router, prefix="/v1")

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if server.started and server.servers:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn failed to start"

    sock = next(iter(server.servers[0].sockets))
    host, port = sock.getsockname()[:2]
    base_url = f"http://{host}:{port}"

    try:
        yield app, base_url, factory
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        engine.dispose()


class TestRunStream:
    def test_backfill_then_live_tail(self, pg_app) -> None:
        _, base_url, factory = pg_app
        run_id = f"sse-{int(time.time() * 1000)}"

        with Session(factory.kw["bind"]) as s:
            emit_event(s, kind="run.created", run_id=run_id, payload={"phase": "backfill"})
            s.commit()

        received: list[dict] = []
        live_signal = threading.Event()

        def consume() -> None:
            with httpx.Client(timeout=10.0, trust_env=False) as client:
                with client.stream("GET", f"{base_url}/v1/runs/{run_id}/stream") as resp:
                    assert resp.status_code == 200
                    assert resp.headers["content-type"].startswith("text/event-stream")
                    buffer = ""
                    for chunk in resp.iter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            frame, buffer = buffer.split("\n\n", 1)
                            if not frame.strip() or frame.startswith(":"):
                                if "ready" in frame:
                                    live_signal.set()
                                continue
                            data_lines = [
                                line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:")
                            ]
                            if not data_lines:
                                continue
                            received.append(json.loads("\n".join(data_lines)))
                            if len(received) >= 2:
                                return

        t = threading.Thread(target=consume, daemon=True)
        t.start()

        assert live_signal.wait(timeout=5), "stream never sent :ready sentinel"

        with Session(factory.kw["bind"]) as s:
            emit_event(s, kind="packet.translated", run_id=run_id, packet_id="p-live", payload={"phase": "live"})
            s.commit()

        t.join(timeout=5)
        assert len(received) == 2, f"expected 2 events, got {received}"
        kinds = [e["kind"] for e in received]
        assert kinds == ["run.created", "packet.translated"]
        assert received[0]["payload"]["phase"] == "backfill"
        assert received[1]["payload"]["phase"] == "live"
        assert received[1]["packet_id"] == "p-live"
