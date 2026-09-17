"""Atomic artifact writes and a per-artifact writer lock for exports.

Readers (downloads, upstream exports, the blob stamper) must never see a
half-written file, and two exports of the same artifact must not interleave.

- ``atomic_path(target)`` yields a temporary sibling path; the caller writes
  it completely (text, zip, a browser-rendered PDF) and on success it is
  renamed over the target with ``os.replace``, which is atomic on POSIX and
  Windows for paths on one filesystem. On failure the temporary file is
  removed and the previous artifact stays untouched.
- ``export_lock(output_dir, key)`` serialises writers of one artifact across
  threads and processes on the same host (``fcntl.flock`` on a lock file in
  the system temp directory; a thread lock only where ``fcntl`` is
  unavailable).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:  # pragma: no cover - platform dependent
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


@contextmanager
def atomic_path(target: Path) -> Iterator[Path]:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        yield temporary
        if not temporary.exists():
            raise FileNotFoundError(f"atomic write produced no file for {target}")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def atomic_write_text(target: Path, text: str, *, encoding: str = "utf-8") -> None:
    with atomic_path(target) as temporary:
        with temporary.open("w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())


def atomic_write_bytes(target: Path, data: bytes) -> None:
    with atomic_path(target) as temporary:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())


def _lock_path(output_dir: Path, key: str) -> Path:
    # Lock files live outside the export tree so listings, bundles and downloads never see them.
    digest = hashlib.sha256(str(output_dir.resolve()).encode("utf-8")).hexdigest()[:24]
    lock_dir = Path(tempfile.gettempdir()) / "book-agent-export-locks" / digest
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_key = "".join(char if char.isalnum() or char in "-_" else "_" for char in key)
    return lock_dir / f"{safe_key}.lock"


@contextmanager
def export_lock(output_dir: Path, key: str) -> Iterator[None]:
    lock_path = _lock_path(output_dir, key)
    with _thread_locks_guard:
        thread_lock = _thread_locks.setdefault(str(lock_path), threading.Lock())
    with thread_lock:
        if fcntl is None:  # pragma: no cover
            yield
            return
        with lock_path.open("a") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
