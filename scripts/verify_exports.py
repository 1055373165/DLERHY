"""Verify and refresh export integrity stamps (M2.1).

For every serviceable ``exports`` row — ``status='succeeded'``,
``stale_reason IS NULL``, and ``file_path`` is not the
``unrecoverable://`` sentinel — re-hash the on-disk bytes and compare
against the stored ``content_sha256``. Outcomes:

* **match** → bump ``last_verified_at``. The bytes on disk still
  match the recorded digest, so the row remains serviceable for
  another TTL window.
* **mismatch** → set ``stale_reason='content_drift'``. Something
  rewrote the file out-of-band (manual edit, rsync, disk error,
  restore from a half-bad backup). Row becomes 410 Gone for
  downloaders until an operator intervenes.
* **missing** → set ``stale_reason='missing_at_verify'``. File
  disappeared between backfill and this scan.

Rows whose ``last_verified_at`` is within the TTL window are skipped
— verification is the *maintenance* path, not the hot path. A row
still gets verified on the first scan after M1.5 backfill because
that's when the stamp was set.

Scope boundaries:

* This script does NOT compute a hash for rows with
  ``content_sha256 IS NULL`` unless ``--hash-null`` is passed —
  that's the backfill's job (``backfill_export_paths.py``) and
  mixing the two obscures which step found drift.
* This script never rewrites ``file_path`` or triggers a rebuild.
  Both are explicit operator actions elsewhere.
"""

from __future__ import annotations

import argparse
import hashlib
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


_SHA256_CHUNK = 1 << 20


@dataclass(slots=True)
class VerifyOutcome:
    record_id: str
    document_id: str
    export_type: str
    file_path: str
    kind: str  # "match" | "mismatch" | "missing"
    observed_sha: str | None
    expected_sha: str | None


@dataclass(slots=True)
class VerifyReport:
    matched: list[VerifyOutcome]
    mismatched: list[VerifyOutcome]
    missing: list[VerifyOutcome]
    skipped_fresh: int
    skipped_unverifiable: int  # NULL sha256 + not --hash-null


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


def _scan(session, *, ttl_days: int, hash_null: bool) -> VerifyReport:
    now = datetime.now(timezone.utc)
    ttl_cutoff = now - timedelta(days=ttl_days)
    rows = session.execute(
        text(
            "SELECT id, document_id, export_type, file_path, "
            "       content_sha256, last_verified_at, stale_reason "
            "FROM exports WHERE status = 'succeeded'"
        )
    ).all()
    matched: list[VerifyOutcome] = []
    mismatched: list[VerifyOutcome] = []
    missing: list[VerifyOutcome] = []
    skipped_fresh = 0
    skipped_unverifiable = 0
    for (
        record_id,
        document_id,
        export_type,
        file_path,
        stored_sha,
        last_verified,
        stale_reason,
    ) in rows:
        fp = str(file_path)
        if stale_reason or fp.startswith("unrecoverable://"):
            continue
        if stored_sha is None and not hash_null:
            skipped_unverifiable += 1
            continue
        if last_verified is not None and last_verified > ttl_cutoff:
            skipped_fresh += 1
            continue
        resolved = _resolve(fp)
        if not resolved.exists():
            missing.append(
                VerifyOutcome(
                    record_id=str(record_id),
                    document_id=str(document_id),
                    export_type=str(export_type),
                    file_path=fp,
                    kind="missing",
                    observed_sha=None,
                    expected_sha=stored_sha,
                )
            )
            continue
        observed = _digest(resolved)
        if stored_sha is None or observed == stored_sha:
            matched.append(
                VerifyOutcome(
                    record_id=str(record_id),
                    document_id=str(document_id),
                    export_type=str(export_type),
                    file_path=fp,
                    kind="match",
                    observed_sha=observed,
                    expected_sha=stored_sha,
                )
            )
        else:
            mismatched.append(
                VerifyOutcome(
                    record_id=str(record_id),
                    document_id=str(document_id),
                    export_type=str(export_type),
                    file_path=fp,
                    kind="mismatch",
                    observed_sha=observed,
                    expected_sha=stored_sha,
                )
            )
    return VerifyReport(
        matched=matched,
        mismatched=mismatched,
        missing=missing,
        skipped_fresh=skipped_fresh,
        skipped_unverifiable=skipped_unverifiable,
    )


def _apply(session, report: VerifyReport) -> None:
    now = datetime.now(timezone.utc)
    for outcome in report.matched:
        # Row with NULL stored sha + --hash-null: also seed the sha.
        if outcome.expected_sha is None:
            session.execute(
                text(
                    "UPDATE exports SET "
                    "  content_sha256 = :sha, last_verified_at = :now "
                    "WHERE id = :id"
                ),
                {"sha": outcome.observed_sha, "now": now, "id": outcome.record_id},
            )
        else:
            session.execute(
                text("UPDATE exports SET last_verified_at = :now WHERE id = :id"),
                {"now": now, "id": outcome.record_id},
            )
    for outcome in report.mismatched:
        session.execute(
            text(
                "UPDATE exports SET stale_reason = COALESCE(stale_reason, 'content_drift') "
                "WHERE id = :id"
            ),
            {"id": outcome.record_id},
        )
    for outcome in report.missing:
        session.execute(
            text(
                "UPDATE exports SET stale_reason = COALESCE(stale_reason, 'missing_at_verify') "
                "WHERE id = :id"
            ),
            {"id": outcome.record_id},
        )
    session.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Mutate the DB. Default is dry-run.")
    parser.add_argument(
        "--ttl-days",
        type=int,
        default=7,
        help="Skip rows verified within this many days (default: 7).",
    )
    parser.add_argument(
        "--hash-null",
        action="store_true",
        help="Also hash rows with NULL content_sha256 (normally left for the backfill).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = build_engine(str(settings.database_url))
    session_factory = build_session_factory(engine=engine)

    with session_factory() as session:
        report = _scan(session, ttl_days=args.ttl_days, hash_null=args.hash_null)
        print(f"matched              : {len(report.matched)}")
        print(f"mismatched (drift)   : {len(report.mismatched)}")
        print(f"missing              : {len(report.missing)}")
        print(f"skipped (fresh)      : {report.skipped_fresh}")
        print(f"skipped (null sha)   : {report.skipped_unverifiable}")
        for outcome in report.mismatched:
            print(
                f"  DRIFT id={outcome.record_id} type={outcome.export_type}\n"
                f"        expected={outcome.expected_sha}\n"
                f"        observed={outcome.observed_sha}\n"
                f"        path={outcome.file_path}"
            )
        for outcome in report.missing:
            print(
                f"  MISS  id={outcome.record_id} doc={outcome.document_id}\n"
                f"        path={outcome.file_path}"
            )
        if args.apply:
            _apply(session, report)
            print(
                f"\napplied {len(report.matched)} refresh(es), "
                f"{len(report.mismatched)} drift-mark(s), "
                f"{len(report.missing)} missing-mark(s)."
            )
        else:
            print("\ndry-run (no mutations). pass --apply to write.")
    # Non-zero exit when any row needs operator attention.
    return 0 if not (report.mismatched or report.missing) else 2


if __name__ == "__main__":
    raise SystemExit(main())
