"""Content-addressable blob store helpers (M2.2c).

After an export writer has successfully materialized bytes at its
canonical ``file_path``, this module is responsible for:

1. Hashing those bytes and stamping ``content_sha256`` / ``byte_count``
   / ``last_verified_at`` on the corresponding ``exports`` row.
2. Hardlinking the bytes into ``<blob_root>/<aa>/<bb>/<sha256>`` so
   the resolver (M2.2b) can serve from the CAS tree and the reaper
   (M2.3) can treat the refcount as authoritative.

Design policy:

* **Best-effort, non-fatal.** If hashing fails (I/O error) or the
  blob link fails (cross-device + copy falls back), we log and
  continue. An export that is missing its integrity stamp is not a
  user-visible failure — the verifier (M2.1) will catch it on the
  next scan, and the resolver has a full canonical-path fallback.
  The alternative — failing the export request because we couldn't
  hardlink — would be a regression from M0 behavior.

* **Idempotent.** Re-running on a row that already has a matching
  sha256 is a no-op (the same-inode check short-circuits). This
  makes it safe to call from every write path without worrying
  about double-stamping.

* **Stays out of the hot-path commit.** Callers should invoke this
  BEFORE they commit the transaction so the sha256 stamp and the
  file I/O land atomically from the reader's perspective. But the
  helper never opens its own transaction — it writes on the session
  it was handed.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import shutil
from datetime import datetime, timezone
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from book_agent.domain.models.review import Export


_SHA256_CHUNK = 1 << 20
UNRECOVERABLE_SCHEME = "unrecoverable://"
_logger = logging.getLogger(__name__)


def blob_target(blob_root: Path, sha256: str) -> Path:
    """Return the canonical on-disk path for a blob given its sha256."""
    return blob_root / sha256[:2] / sha256[2:4] / sha256


def _digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_SHA256_CHUNK)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _same_inode(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except FileNotFoundError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def _hardlink_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    try:
        os.link(source, target)
        return
    except OSError as exc:
        if exc.errno not in {errno.EXDEV, errno.EPERM}:
            raise
    shutil.copyfile(source, target)


def stamp_and_materialize(
    session: Session,
    *,
    export_id: str,
    file_path: Path,
    blob_root: Path,
) -> str | None:
    """Hash the file, stamp the export row, hardlink into the CAS tree.

    Returns the sha256 on success, or ``None`` if anything went
    wrong (always logged). Never raises — callers should treat CAS
    emission as advisory, not a hard prerequisite for export success.
    """
    if not file_path.exists():
        _logger.warning(
            "blob stamp skipped: file_path does not exist",
            extra={"export_id": export_id, "file_path": str(file_path)},
        )
        return None
    try:
        sha256, byte_count = _digest(file_path)
    except OSError as exc:
        _logger.warning(
            "blob stamp skipped: hashing failed",
            extra={"export_id": export_id, "file_path": str(file_path), "error": str(exc)},
        )
        return None
    target = blob_target(blob_root, sha256)
    if not _same_inode(file_path, target):
        try:
            _hardlink_or_copy(file_path, target)
        except OSError as exc:
            _logger.warning(
                "blob materialize failed; stamping sha256 anyway so verifier can pick it up",
                extra={"export_id": export_id, "target": str(target), "error": str(exc)},
            )
    # ORM-level UPDATE so the id is bound through the column type (SQLite stores
    # UUIDs without dashes) and already-loaded Export objects stay in sync.
    session.execute(
        update(Export)
        .where(Export.id == export_id)
        .values(
            content_sha256=sha256,
            byte_count=byte_count,
            last_verified_at=datetime.now(timezone.utc),
        )
    )
    return sha256


def blob_root_for_export_root(export_root: str | Path) -> Path:
    """The CAS tree lives under the artifact root, i.e. the parent of ``export_root``."""
    resolved = Path(export_root).resolve()
    return resolved.parent.resolve() / "blobs"


def stamp_export_records(
    session: Session,
    records: Iterable[Any],
    *,
    blob_root: Path,
    resolve_path: Callable[[Any], Path | None] | None = None,
) -> None:
    """Stamp and materialize every export record whose file is on disk.

    ``resolve_path`` lets callers heal legacy ``file_path`` values that are no
    longer absolute or reachable. Best-effort like ``stamp_and_materialize``.
    """
    for record in records:
        candidate = str(getattr(record, "file_path", "") or "")
        if not candidate or candidate.startswith(UNRECOVERABLE_SCHEME):
            continue
        path = Path(candidate)
        if (not path.is_absolute() or not path.exists()) and resolve_path is not None:
            path = resolve_path(record)
        if path is None:
            continue
        stamp_and_materialize(session, export_id=str(record.id), file_path=path, blob_root=blob_root)


__all__ = [
    "UNRECOVERABLE_SCHEME",
    "blob_root_for_export_root",
    "blob_target",
    "stamp_and_materialize",
    "stamp_export_records",
]
