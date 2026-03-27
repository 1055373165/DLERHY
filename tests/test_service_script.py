import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = ROOT / "service.sh"


class ServiceScriptTests(unittest.TestCase):
    def _write_fake_uv(self, tempdir: Path, capture_path: Path) -> Path:
        fake_bin = tempdir / "bin"
        fake_bin.mkdir(parents=True, exist_ok=True)
        fake_uv = fake_bin / "uv"
        fake_uv.write_text(
            """#!/usr/bin/env bash
set -euo pipefail

cmd="${1:-}"
if [[ "$cmd" == "sync" ]]; then
    exit 0
fi

if [[ "$cmd" == "run" ]]; then
    tool="${2:-}"
    if [[ "$tool" == "uvicorn" ]]; then
        printf '%s' "${BOOK_AGENT_DATABASE_URL:-}" > "${FAKE_UV_CAPTURE:?}"
        sleep 3
        exit 0
    fi
    exit 0
fi

exit 0
""",
            encoding="utf-8",
        )
        fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IXUSR)
        return fake_bin

    def _run_service(self, tempdir: Path, fake_bin: Path, *args: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            ["bash", "service.sh", *args],
            cwd=tempdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def _copy_service_script(self, tempdir: Path) -> None:
        destination = tempdir / "service.sh"
        destination.write_text(SERVICE_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR)

    def test_default_sqlite_mode_overrides_non_sqlite_database_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tempdir = Path(tmpdir)
            capture_path = tempdir / "captured-database-url.txt"
            self._copy_service_script(tempdir)
            fake_bin = self._write_fake_uv(tempdir, capture_path)

            result = self._run_service(
                tempdir,
                fake_bin,
                "start",
                env_overrides={
                    "BOOK_AGENT_DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:9/book_agent",
                    "FAKE_UV_CAPTURE": str(capture_path),
                },
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(capture_path.exists())
            resolved_tempdir = tempdir.resolve()
            self.assertEqual(
                capture_path.read_text(encoding="utf-8"),
                f"sqlite+pysqlite:///{resolved_tempdir}/artifacts/book-agent.db",
            )
            self.assertIn("Ignoring existing non-SQLite BOOK_AGENT_DATABASE_URL", result.stdout)

            time.sleep(3.1)
            self._run_service(
                tempdir,
                fake_bin,
                "stop",
                env_overrides={"FAKE_UV_CAPTURE": str(capture_path)},
            )

    def test_default_sqlite_mode_preserves_explicit_sqlite_database_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tempdir = Path(tmpdir)
            capture_path = tempdir / "captured-database-url.txt"
            explicit_sqlite_url = f"sqlite+pysqlite:///{tempdir}/custom.sqlite"
            self._copy_service_script(tempdir)
            fake_bin = self._write_fake_uv(tempdir, capture_path)

            result = self._run_service(
                tempdir,
                fake_bin,
                "start",
                env_overrides={
                    "BOOK_AGENT_DATABASE_URL": explicit_sqlite_url,
                    "FAKE_UV_CAPTURE": str(capture_path),
                },
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(capture_path.exists())
            self.assertEqual(capture_path.read_text(encoding="utf-8"), explicit_sqlite_url)

            time.sleep(3.1)
            self._run_service(
                tempdir,
                fake_bin,
                "stop",
                env_overrides={"FAKE_UV_CAPTURE": str(capture_path)},
            )
