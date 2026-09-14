from pathlib import Path
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Redirect tempfile root to a project-local directory so the
# `exports_file_path_no_tempdir_check` CHECK constraint (which rejects
# /var/folders/* and /tmp/* prefixes to keep production clean) doesn't
# trip when tests use `tempfile.TemporaryDirectory()` on macOS.
# The constraint is intentionally strict for prod; the test environment
# satisfies it by living under the repo's `.test-tmp/` rather than the OS
# default. See domain/models/review.py:120 for the constraint definition.
# Tests must never call a real translation provider configured in the
# developer's .env (process env beats dotenv in Settings). Tests that need a
# provider-backed worker construct Settings or clients explicitly.
os.environ["BOOK_AGENT_TRANSLATION_BACKEND"] = "echo"
os.environ["BOOK_AGENT_TRANSLATION_MODEL"] = "echo-worker"

_PROJECT_TMP_ROOT = ROOT / ".test-tmp"
_PROJECT_TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["TMPDIR"] = str(_PROJECT_TMP_ROOT)
tempfile.tempdir = str(_PROJECT_TMP_ROOT)

