"""MCP stdio server: protocol handshake, tool listing, read and reversible tools, errors, CLI transport."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.domain.enums import LockLevel
from book_agent.domain.models.translation import TermEntry
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.mcp.server import McpServer
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

ROOT = Path(__file__).resolve().parents[1]


def _call(server: McpServer, name: str, arguments: dict, request_id: int = 1) -> dict:
    return server.handle({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}})["result"]


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.database_url = f"sqlite+pysqlite:///{root / 'mcp.db'}"
        engine = build_engine(self.database_url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)
        epub = root / "book.epub"
        with zipfile.ZipFile(epub, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        artifacts = BootstrapOrchestrator().bootstrap_epub(epub)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        self.document_id = artifacts.document.id
        self.server = McpServer(self.session_factory)

    def test_handshake_and_tool_list_exclude_irreversible_tools(self) -> None:
        init = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        self.assertIn("tools", init["result"]["capabilities"])
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        tools = {tool["name"]: tool for tool in self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
        self.assertIn("search_book", tools)
        self.assertIn("document_id", tools["search_book"]["inputSchema"]["required"])
        self.assertNotIn("lock_term", tools)
        self.assertFalse(tools["propose_term"]["annotations"]["readOnlyHint"])
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"], -32601)

    def test_read_tools_and_a_reversible_write(self) -> None:
        listed = _call(self.server, "list_documents", {})
        self.assertFalse(listed["isError"])
        self.assertEqual(listed["structuredContent"]["documents"][0]["document_id"], self.document_id)
        found = _call(self.server, "search_book", {"document_id": self.document_id, "query": "context engineering"})
        self.assertGreaterEqual(found["structuredContent"]["total_sentences"], 1)
        summary = _call(self.server, "document_summary", {"document_id": self.document_id})
        self.assertFalse(summary["isError"], summary)
        proposed = _call(self.server, "propose_term", {"document_id": self.document_id, "source_term": "context engineering", "target_term": "上下文工程"})
        self.assertFalse(proposed["isError"], proposed)
        with self.session_factory() as session:
            entry = session.scalars(select(TermEntry).where(TermEntry.source_term == "context engineering")).one()
            self.assertEqual(entry.lock_level, LockLevel.PREFERRED)
        guide = _call(self.server, "book_guide", {"document_id": self.document_id})
        self.assertIn("上下文工程", guide["structuredContent"]["markdown"])

    def test_errors_are_tool_results(self) -> None:
        self.assertTrue(_call(self.server, "no_such_tool", {})["isError"])
        self.assertTrue(_call(self.server, "search_book", {"query": "x"})["isError"])
        missing = _call(self.server, "search_book", {"document_id": "00000000-0000-0000-0000-000000000000", "query": "x"})
        self.assertIn("document not found", missing["content"][0]["text"])

    def test_stdio_transport_including_parse_errors(self) -> None:
        stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n\n')
        stdout = io.StringIO()
        self.server.serve(stdin, stdout)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(lines[0], {"jsonrpc": "2.0", "id": 1, "result": {}})
        self.assertEqual(lines[1]["error"]["code"], -32700)

    def test_cli_serves_over_stdio(self) -> None:
        requests = "\n".join(
            json.dumps(message)
            for message in (
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_documents", "arguments": {}}},
            )
        ) + "\n"
        completed = subprocess.run(
            [sys.executable, "-m", "book_agent.cli", "--database-url", self.database_url, "mcp"],
            input=requests,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=ROOT,
        )
        responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual([response["id"] for response in responses], [1, 2], completed.stderr[-2000:])
        self.assertIn(self.document_id, responses[1]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
