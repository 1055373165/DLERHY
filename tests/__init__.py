import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Tests must never call a real translation provider configured in the
# developer's .env (process env beats dotenv in Settings). Tests that need a
# provider-backed worker construct Settings or clients explicitly.
os.environ["BOOK_AGENT_TRANSLATION_BACKEND"] = "echo"
os.environ["BOOK_AGENT_TRANSLATION_MODEL"] = "echo-worker"
# Without a configured key, services.secrets generates one and writes it into
# the project .env; give tests a fixed throwaway Fernet key instead.
os.environ["BOOK_AGENT_SECRET_KEY"] = "dGVzdC1vbmx5LWZlcm5ldC1rZXktMzItYnl0ZXMhISE="

# Redirect tempfile root to a project-local directory so the
# `exports_file_path_no_tempdir_check` CHECK constraint (which rejects
# /var/folders/* and /tmp/* prefixes to keep production clean) doesn't
# trip when tests use `tempfile.TemporaryDirectory()` on macOS.
# The constraint is intentionally strict for prod; the test environment
# satisfies it by living under the repo's `.test-tmp/` rather than the OS
# default. See domain/models/review.py:120 for the constraint definition.
#
# Each test process gets its own directory, removed at exit, so leaked temp
# files (e.g. extractor image dirs) do not pile up. The parent `.test-tmp/`
# also holds hand-curated data used by delivery scripts; never clear it.
_PROJECT_TMP_ROOT = Path(tempfile.mkdtemp(prefix=f"pytest-{os.getpid()}-", dir=_ensure_dir(ROOT / ".test-tmp")))
os.environ["TMPDIR"] = str(_PROJECT_TMP_ROOT)
tempfile.tempdir = str(_PROJECT_TMP_ROOT)
atexit.register(shutil.rmtree, _PROJECT_TMP_ROOT, ignore_errors=True)

