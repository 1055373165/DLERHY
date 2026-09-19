"""kill -9 drill: a whole-book run survives the server being killed mid-translation.

Starts the app (SQLite, echo translator) in a subprocess, uploads a generated
book, starts a full run, SIGKILLs the server once some packets are
translated, starts a fresh server on the same database and waits for the run
to finish. Then checks that every sentence was translated exactly once and
the Chinese HTML downloads.

    uv run python scripts/crash_recovery_drill.py [--chapters 40] [--kills 1]

Run ownership is shortened to 5s so the new server takes the run over
quickly (default 30s). The packet that was in flight when the server died
keeps its 120s work-item lease, so it is picked up again after that.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _book(path: Path, chapters: int, paragraphs: int) -> None:
    items, spine, nav = [], [], []
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        for number in range(1, chapters + 1):
            name = f"ch{number}.xhtml"
            body = "".join(
                f"<p>Paragraph {p} of chapter {number} explains how a steady company plans its next quarter. "
                f"It weighs cost against growth, and it keeps the promise it made in paragraph {p}.</p>"
                for p in range(1, paragraphs + 1)
            )
            archive.writestr(
                f"OEBPS/{name}",
                '<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
                f"<title>Chapter {number}</title></head><body><h1>Chapter {number}</h1>{body}</body></html>",
            )
            items.append(f'<item id="c{number}" href="{name}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{number}"/>')
            nav.append(f'<li><a href="{name}">Chapter {number}</a></li>')
        archive.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body>'
            f'<nav epub:type="toc"><ol>{"".join(nav)}</ol></nav></body></html>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:identifier id="id">urn:uuid:{uuid.uuid4()}</dc:identifier><dc:title>Crash Drill Handbook</dc:title>'
            "<dc:creator>Drill Author</dc:creator><dc:language>en</dc:language></metadata><manifest>"
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            f'{"".join(items)}</manifest><spine>{"".join(spine)}</spine></package>',
        )


class Server:
    def __init__(self, workdir: Path, port: int) -> None:
        self.workdir, self.port, self.process = workdir, port, None
        self.base = f"http://127.0.0.1:{port}/v1"

    def start(self) -> None:
        env = {
            **os.environ,
            "BOOK_AGENT_APP_SCOPE": "e2e",
            "BOOK_AGENT_DATABASE_URL": f"sqlite+pysqlite:///{self.workdir / 'app.db'}",
            "BOOK_AGENT_TRANSLATION_BACKEND": "echo",
            "BOOK_AGENT_TRANSLATION_MODEL": "echo-worker",
            "BOOK_AGENT_TRANSLATION_OPENAI_API_KEY": "",
            "BOOK_AGENT_AUTH_MODE": "disabled",
            "BOOK_AGENT_EXPORT_ROOT": str(self.workdir / "exports"),
            "BOOK_AGENT_UPLOAD_ROOT": str(self.workdir / "uploads"),
            "BOOK_AGENT_RUN_OWNERSHIP_TTL_SECONDS": "5",
        }
        log = open(self.workdir / "server.log", "a")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "book_agent.app.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                self.get("/health")
                return
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.3)
        raise RuntimeError("server did not start; see server.log")

    def kill(self) -> None:
        os.kill(self.process.pid, signal.SIGKILL)
        self.process.wait()

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=30)

    def get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=30) as response:
            return json.loads(response.read())

    def post(self, path: str, body: dict | None = None, *, data: bytes | None = None, headers: dict | None = None):
        request = urllib.request.Request(
            self.base + path,
            data=data if data is not None else json.dumps(body or {}).encode(),
            headers=headers or {"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())

    def upload(self, path: Path) -> dict:
        boundary = uuid.uuid4().hex
        payload = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="source_file"; filename="{path.name}"\r\n'
            "Content-Type: application/epub+zip\r\n\r\n"
        ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        return self.post(
            "/documents/bootstrap-upload",
            data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )


def _create_schema(database: Path) -> None:
    from sqlalchemy import create_engine

    import book_agent.domain.models  # noqa: F401
    from book_agent.infra.db.base import Base

    engine = create_engine(f"sqlite+pysqlite:///{database}")
    Base.metadata.create_all(engine)
    engine.dispose()


def _translated_packets(database: Path, run_id: str) -> int:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "select count(*) from work_items where run_id = ? and stage = 'translate' and status = 'succeeded'",
            (run_id.replace("-", ""),),
        ).fetchone()[0]


def _coverage(database: Path, document_id: str) -> tuple[int, int, int]:
    """(translatable sentences, sentences with an active target, sentences with more than one)."""
    with sqlite3.connect(database) as connection:
        document = document_id.replace("-", "")
        total = connection.execute(
            "select count(*) from sentences where document_id = ? and translatable = 1", (document,)
        ).fetchone()[0]
        rows = connection.execute(
            """
            select s.id, count(distinct e.target_segment_id)
            from sentences s
            join alignment_edges e on e.sentence_id = s.id
            join target_segments t on t.id = e.target_segment_id
            where s.document_id = ? and s.translatable = 1 and t.final_status != 'superseded'
            group by s.id
            """,
            (document,),
        ).fetchall()
    return total, len(rows), sum(1 for _, count in rows if count > 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chapters", type=int, default=40)
    parser.add_argument("--paragraphs", type=int, default=30)
    parser.add_argument("--kills", type=int, default=1)
    parser.add_argument("--port", type=int, default=58231)
    parser.add_argument("--workdir", type=Path, default=ROOT / ".test-tmp" / "crash-drill")
    args = parser.parse_args()

    workdir = args.workdir
    if workdir.exists():
        for path in sorted(workdir.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
    workdir.mkdir(parents=True, exist_ok=True)
    _create_schema(workdir / "app.db")
    book = workdir / "drill.epub"
    _book(book, args.chapters, args.paragraphs)

    server = Server(workdir, args.port)
    server.start()
    try:
        document = server.upload(book)
        document_id = document["document_id"]
        run = server.post(
            "/runs", {"document_id": document_id, "run_type": "translate_full", "requested_by": "crash-drill"}
        )
        run_id = run["run_id"]
        server.post(f"/runs/{run_id}/resume", {"actor_id": "crash-drill"})
        total_packets = document.get("packet_count")
        print(f"document {document_id}: {total_packets} packets; run {run_id}")

        for kill in range(1, args.kills + 1):
            target = (total_packets or 100) * kill // (args.kills + 1)
            while _translated_packets(workdir / "app.db", run_id) < max(1, target):
                if server.get(f"/runs/{run_id}")["status"] not in {"queued", "running"}:
                    raise RuntimeError("run finished before it could be killed; use a bigger book")
                time.sleep(0.2)
            done = _translated_packets(workdir / "app.db", run_id)
            server.kill()
            print(f"kill {kill}: SIGKILL after {done} translated packets")
            started = time.time()
            server.start()
            while _translated_packets(workdir / "app.db", run_id) <= done:
                if time.time() - started > 300:
                    raise RuntimeError("no progress within 5 minutes of the restart")
                time.sleep(0.5)
            print(f"kill {kill}: restarted server resumed translating after {time.time() - started:.1f}s")

        deadline = time.time() + 900
        while (status := server.get(f"/runs/{run_id}")["status"]) in {"queued", "running"}:
            if time.time() > deadline:
                raise RuntimeError("run did not finish within 15 minutes")
            time.sleep(1)
        total, covered, duplicated = _coverage(workdir / "app.db", document_id)
        print(f"run status: {status}; sentences translated {covered}/{total}; translated more than once: {duplicated}")
        with urllib.request.urlopen(
            f"{server.base}/documents/{document_id}/exports/download?export_type=merged_html&package=single", timeout=120
        ) as response:
            html = response.read()
        print(f"merged HTML: {len(html)} bytes")
        ok = status in {"succeeded", "succeeded_with_warnings"} and covered == total and duplicated == 0 and html
        print("DRILL PASSED" if ok else "DRILL FAILED")
        return 0 if ok else 1
    finally:
        server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
