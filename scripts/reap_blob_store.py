"""Delete orphan blobs from the content-addressable store (M2.3).

The CAS tree under ``artifacts/blobs/<aa>/<bb>/<sha>`` is populated by
the writer (M2.2c) and the one-shot backfill (M2.2a). A blob becomes
an *orphan* the moment no serviceable ``exports`` row references its
sha256 — e.g. a row was hard-deleted, re-exported to new bytes, or
marked ``stale_reason='content_drift'`` (which takes it out of the
reader's preference set). Orphans are disk-only waste; nothing will
ever link to them again.

This script:

1. Enumerates every file under ``--blob-root`` whose path matches the
   ``<aa>/<bb>/<sha>`` layout.
2. Loads the set of referenced sha256s from the DB — rows that are
   ``status='succeeded'`` with a non-NULL ``content_sha256`` and no
   ``stale_reason`` and whose ``file_path`` is not the
   ``unrecoverable://`` sentinel.
3. For each blob not in the referenced set, checks its mtime is older
   than ``--min-age-minutes`` (default 60) and — if ``--apply`` was
   passed — unlinks it.

Why the age gate: a new export first writes the canonical file, then
hashes it, then hardlinks into the CAS tree, then stamps the DB row in
the SAME transaction. Between the link and the commit there is a
*very* narrow window where a reaper running in another process could
see the blob without seeing the row. The age gate makes that race
impossible to lose — a 60-minute-old blob cannot still be mid-export.

Why we do NOT prune empty ``<aa>/<bb>/`` dirs by default: the writer
creates them on demand with ``mkdir(parents=True, exist_ok=True)``, so
leaving empty dirs around costs inodes but no code. Pass
``--prune-dirs`` if you want them removed.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import text

from book_agent.core.config import get_settings
from book_agent.infra.db.session import build_engine, build_session_factory


# A blob filename is the 64-char lowercase hex sha256.
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(slots=True)
class Orphan:
    sha256: str
    path: Path
    size: int
    age_seconds: float


@dataclass(slots=True)
class ReapReport:
    scanned: int
    referenced: int
    orphans: list[Orphan]
    too_young: list[Orphan]
    removed: list[Orphan]
    reclaimed_bytes: int


def _walk_blobs(blob_root: Path) -> list[Path]:
    # The layout is exactly two 2-char prefix dirs then the sha file.
    # We rely on the structure instead of an unbounded rglob so that a
    # stray file (someone's temp scratch) under artifacts/blobs won't
    # be considered for reaping.
    hits: list[Path] = []
    if not blob_root.is_dir():
        return hits
    for aa in blob_root.iterdir():
        if not aa.is_dir() or len(aa.name) != 2:
            continue
        for bb in aa.iterdir():
            if not bb.is_dir() or len(bb.name) != 2:
                continue
            for blob in bb.iterdir():
                if blob.is_file() and _SHA_RE.match(blob.name):
                    hits.append(blob)
    return hits


def _referenced_shas(session) -> set[str]:
    rows = session.execute(
        text(
            "SELECT DISTINCT content_sha256 FROM exports "
            "WHERE status = 'succeeded' "
            "  AND content_sha256 IS NOT NULL "
            "  AND stale_reason IS NULL "
            "  AND file_path NOT LIKE 'unrecoverable://%'"
        )
    ).all()
    return {str(r[0]) for r in rows if r[0]}


def _scan(
    session,
    blob_root: Path,
    *,
    min_age: timedelta,
) -> ReapReport:
    now = datetime.now(timezone.utc)
    blobs = _walk_blobs(blob_root)
    referenced = _referenced_shas(session)
    orphans: list[Orphan] = []
    too_young: list[Orphan] = []
    for blob in blobs:
        if blob.name in referenced:
            continue
        st = blob.stat()
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        age = now - mtime
        outcome = Orphan(
            sha256=blob.name,
            path=blob,
            size=st.st_size,
            age_seconds=age.total_seconds(),
        )
        if age < min_age:
            too_young.append(outcome)
        else:
            orphans.append(outcome)
    return ReapReport(
        scanned=len(blobs),
        referenced=len(referenced),
        orphans=orphans,
        too_young=too_young,
        removed=[],
        reclaimed_bytes=0,
    )


def _prune_empty_dirs(blob_root: Path) -> int:
    pruned = 0
    for aa in blob_root.iterdir():
        if not aa.is_dir() or len(aa.name) != 2:
            continue
        for bb in aa.iterdir():
            if not bb.is_dir() or len(bb.name) != 2:
                continue
            try:
                bb.rmdir()
                pruned += 1
            except OSError:
                pass
        try:
            aa.rmdir()
            pruned += 1
        except OSError:
            pass
    return pruned


def _apply(report: ReapReport) -> None:
    for orphan in report.orphans:
        try:
            orphan.path.unlink()
            report.removed.append(orphan)
            report.reclaimed_bytes += orphan.size
        except OSError:
            # Best-effort; if another process already removed it,
            # the next scan will reconcile.
            continue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete orphan blobs. Default is dry-run.",
    )
    parser.add_argument(
        "--blob-root",
        default=str(ROOT / "artifacts" / "blobs"),
        help="Root dir for the CAS tree (default: artifacts/blobs).",
    )
    parser.add_argument(
        "--min-age-minutes",
        type=int,
        default=60,
        help="Skip blobs whose mtime is newer than this (default: 60).",
    )
    parser.add_argument(
        "--prune-dirs",
        action="store_true",
        help="Also remove empty <aa>/<bb>/ prefix dirs after reaping.",
    )
    args = parser.parse_args(argv)

    blob_root = Path(args.blob_root).resolve()
    min_age = timedelta(minutes=args.min_age_minutes)

    settings = get_settings()
    engine = build_engine(str(settings.database_url))
    session_factory = build_session_factory(engine=engine)

    with session_factory() as session:
        report = _scan(session, blob_root, min_age=min_age)
        if args.apply:
            _apply(report)
            if args.prune_dirs:
                report_pruned = _prune_empty_dirs(blob_root)
            else:
                report_pruned = 0
        else:
            report_pruned = 0

        mb = report.reclaimed_bytes / (1024 * 1024)
        print(f"blobs scanned            : {report.scanned}")
        print(f"referenced in DB         : {report.referenced}")
        print(f"orphans (reapable)       : {len(report.orphans)}")
        print(f"orphans (too young)      : {len(report.too_young)}")
        if args.apply:
            print(f"removed                  : {len(report.removed)}")
            print(f"reclaimed                : {mb:.2f} MiB")
            if args.prune_dirs:
                print(f"pruned empty prefix dirs : {report_pruned}")
        else:
            would_bytes = sum(o.size for o in report.orphans) / (1024 * 1024)
            print(f"would-reclaim            : {would_bytes:.2f} MiB")

        for orphan in report.orphans[:20]:
            age_h = orphan.age_seconds / 3600
            print(f"  ORPHAN sha={orphan.sha256} size={orphan.size} age_h={age_h:.1f}")
        if len(report.orphans) > 20:
            print(f"  ... and {len(report.orphans) - 20} more")

        if not args.apply:
            print("\ndry-run (no deletions). pass --apply to reap.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
