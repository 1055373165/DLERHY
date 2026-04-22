"""Populate a content-addressable blob store from existing exports (M2.2a).

For every serviceable export row (``status='succeeded'``,
``content_sha256 IS NOT NULL``, ``stale_reason IS NULL``, not
``unrecoverable://``), materialize a copy of the file at
``artifacts/blobs/<aa>/<bb>/<sha256>`` using hardlinks when possible
(same filesystem, ~zero disk cost) and a byte-copy fallback
otherwise.

Why this script is the *only* thing M2.2a does:

* The blob store becomes a parallel tree. Nothing reads from it yet
  (reader preference is M2.2b). Nothing writes to it from the hot
  path yet (writer retrofit is M2.2c). So populating it is a pure
  additive operation — no schema migration, no code-path change, no
  risk to prod reads.
* Hardlinks make re-running cheap. If a blob already exists at the
  target, we skip. If our source file's inode already matches the
  target inode, we also skip. So this script is idempotent and
  safe to run in cron.
* Verification is built in: before materializing, re-hash the source
  bytes and confirm they match the recorded ``content_sha256``. A
  mismatch is reported and NOT written (operator must re-run the
  verifier to mark the row stale first).

Layout choice (``<aa>/<bb>/<sha>``): two 2-char prefix dirs give us
256×256=65k bucket cardinality which keeps any single directory
comfortably under filesystem-unfriendly sizes even at millions of
blobs. More levels is wasted indirection at our scale.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import text

from book_agent.core.config import get_settings
from book_agent.infra.db.session import build_engine, build_session_factory


_SHA256_CHUNK = 1 << 20


@dataclass(slots=True)
class BlobOutcome:
    record_id: str
    sha256: str
    source: str
    target: str
    kind: str  # "linked" | "copied" | "already_present" | "same_inode" | "hash_mismatch"


@dataclass(slots=True)
class BlobReport:
    actions: list[BlobOutcome]
    hash_mismatches: list[BlobOutcome]
    skipped_missing: int
    skipped_no_sha: int
    skipped_unrecoverable: int


def _blob_target(blob_root: Path, sha256: str) -> Path:
    return blob_root / sha256[:2] / sha256[2:4] / sha256


def _resolve(file_path: str) -> Path:
    p = Path(file_path)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_SHA256_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _same_inode(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except FileNotFoundError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def _materialize(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if _same_inode(source, target):
            return "same_inode"
        return "already_present"
    # Try hardlink first. Cross-device link fails with EXDEV → fall
    # back to copy. EPERM can happen on restrictive filesystems
    # (sandbox mounts) — treat the same way.
    try:
        os.link(source, target)
        return "linked"
    except OSError as exc:
        if exc.errno not in {errno.EXDEV, errno.EPERM}:
            raise
    shutil.copyfile(source, target)
    return "copied"


def _scan(session, blob_root: Path, *, apply: bool) -> BlobReport:
    rows = session.execute(
        text(
            "SELECT id, file_path, content_sha256, stale_reason "
            "FROM exports WHERE status = 'succeeded'"
        )
    ).all()
    actions: list[BlobOutcome] = []
    hash_mismatches: list[BlobOutcome] = []
    skipped_missing = 0
    skipped_no_sha = 0
    skipped_unrecoverable = 0
    for record_id, file_path, sha256, stale_reason in rows:
        fp = str(file_path)
        if stale_reason or fp.startswith("unrecoverable://"):
            skipped_unrecoverable += 1
            continue
        if not sha256:
            skipped_no_sha += 1
            continue
        source = _resolve(fp)
        if not source.exists():
            skipped_missing += 1
            continue
        target = _blob_target(blob_root, str(sha256))
        observed = _digest(source)
        if observed != sha256:
            hash_mismatches.append(
                BlobOutcome(
                    record_id=str(record_id),
                    sha256=str(sha256),
                    source=str(source),
                    target=str(target),
                    kind="hash_mismatch",
                )
            )
            continue
        if apply:
            kind = _materialize(source, target)
        else:
            if target.exists():
                kind = "same_inode" if _same_inode(source, target) else "already_present"
            else:
                kind = "linked"  # best-effort predicted kind; dry-run only
        actions.append(
            BlobOutcome(
                record_id=str(record_id),
                sha256=str(sha256),
                source=str(source),
                target=str(target),
                kind=kind,
            )
        )
    return BlobReport(
        actions=actions,
        hash_mismatches=hash_mismatches,
        skipped_missing=skipped_missing,
        skipped_no_sha=skipped_no_sha,
        skipped_unrecoverable=skipped_unrecoverable,
    )


def _counts(actions: list[BlobOutcome]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in actions:
        out[a.kind] = out.get(a.kind, 0) + 1
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually materialize. Default is dry-run.")
    parser.add_argument(
        "--blob-root",
        default=str(ROOT / "artifacts" / "blobs"),
        help="Root dir for the content-addressable blob tree.",
    )
    args = parser.parse_args(argv)

    blob_root = Path(args.blob_root).resolve()
    settings = get_settings()
    engine = build_engine(str(settings.database_url))
    session_factory = build_session_factory(engine=engine)

    with session_factory() as session:
        report = _scan(session, blob_root, apply=args.apply)
        counts = _counts(report.actions)
        print(f"candidates              : {len(report.actions)}")
        for kind in ("linked", "copied", "already_present", "same_inode"):
            if kind in counts:
                print(f"  {kind:<22}: {counts[kind]}")
        print(f"hash_mismatches         : {len(report.hash_mismatches)}")
        print(f"skipped (missing file)  : {report.skipped_missing}")
        print(f"skipped (no sha256)     : {report.skipped_no_sha}")
        print(f"skipped (unrecoverable) : {report.skipped_unrecoverable}")
        for hm in report.hash_mismatches:
            print(
                f"  MISMATCH id={hm.record_id} expected={hm.sha256}\n"
                f"           source={hm.source}"
            )
        if not args.apply:
            print("\ndry-run (no filesystem changes). pass --apply to materialize.")
    return 0 if not report.hash_mismatches else 2


if __name__ == "__main__":
    raise SystemExit(main())
