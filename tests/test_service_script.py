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
    if [[ "$tool" == "alembic" ]]; then
        printf '%s' "${*:3}" > "${FAKE_ALEMBIC_CAPTURE:?}"
        exit 0
    fi
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

    def test_refuses_sqlite_database_url(self) -> None:
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
                    "BOOK_AGENT_DATABASE_URL": f"sqlite+pysqlite:///{tempdir}/custom.sqlite",
                    "FAKE_UV_CAPTURE": str(capture_path),
                },
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be a PostgreSQL URL", result.stderr)
            self.assertFalse(capture_path.exists())

    def test_external_postgres_url_is_migrated_and_passed_to_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tempdir = Path(tmpdir)
            capture_path = tempdir / "captured-database-url.txt"
            alembic_capture = tempdir / "alembic-args.txt"
            external_url = "postgresql+psycopg://postgres:postgres@db.internal:5432/book_agent"
            self._copy_service_script(tempdir)
            fake_bin = self._write_fake_uv(tempdir, capture_path)

            result = self._run_service(
                tempdir,
                fake_bin,
                "start",
                env_overrides={
                    "BOOK_AGENT_DATABASE_URL": external_url,
                    "FAKE_UV_CAPTURE": str(capture_path),
                    "FAKE_ALEMBIC_CAPTURE": str(alembic_capture),
                },
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertEqual(alembic_capture.read_text(encoding="utf-8"), "upgrade head")
            self.assertEqual(capture_path.read_text(encoding="utf-8"), external_url)
            self.assertNotIn("Docker Compose", result.stdout)

            time.sleep(3.1)
            self._run_service(
                tempdir,
                fake_bin,
                "stop",
                "--keep-db",
                env_overrides={"FAKE_UV_CAPTURE": str(capture_path)},
            )
