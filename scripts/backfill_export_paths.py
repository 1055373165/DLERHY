"""Backfill the M1.2 integrity columns on existing export rows.

For every ``exports`` row that is serviceable — ``status='succeeded'``,
no ``stale_reason``, and ``file_path`` is not the ``unrecoverable://``
sentinel — read the bytes from disk, compute ``sha256`` and
``byte_count``, and stamp ``last_verified_at``. Rows whose file is
missing at scan time get ``stale_reason='missing_at_backfill'`` so the
download path can return 410 Gone for them without ever racing a
read. Dry-run is the default.

Notes:

* Skipped rows where ``content_sha256`` is already populated *unless*
  ``--recompute`` is passed. The verifier (M2) is the long-term owner
  of this field; the backfill only seeds it.
* Paths are resolved against repo root when relative, mirroring
  ``_resolve_artifact_path`` so a relative path stored by the
  executor resolves the same way here.
* A missing file marks the row stale but does NOT rewrite
  ``file_path`` — the heal-by-basename script is the right tool for
  that and runs before this one in the operator playbook.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import text

from book_agent.core.config import get_settings
from book_agent.infra.db.session import build_engine, build_session_factory


_SHA256_CHUNK = 1 << 20  # 1 MiB — modest RAM, one syscall per MiB


@dataclass(slots=True)
class BackfillPlan:
    record_id: str
    document_id: str
    export_type: str
    file_path: str
    sha256: str
    byte_count: int


@dataclass(slots=True)
class StalePlan:
    record_id: str
    document_id: str
    file_path: str
    reason: str


@dataclass(slots=True)
class BackfillReport:
    computed: list[BackfillPlan]
    stale: list[StalePlan]
    already_done: int
    skipped_unrecoverable: int


def _resolve(file_path: str) -> Path:
    p = Path(file_path)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p


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


def _scan(session, *, recompute: bool) -> BackfillReport:
    rows = session.execute(
        text(
            "SELECT id, document_id, export_type, file_path, "
            "       content_sha256, byte_count, last_verified_at, stale_reason "
            "FROM exports WHERE status = 'succeeded'"
        )
    ).all()
    computed: list[BackfillPlan] = []
    stale: list[StalePlan] = []
    already_done = 0
    skipped_unrecoverable = 0
    for (
        record_id,
        document_id,
        export_type,
        file_path,
        existing_sha,
        existing_bytes,
        existing_verified,
        existing_stale,
    ) in rows:
        fp = str(file_path)
        if fp.startswith("unrecoverable://") or existing_stale:
            skipped_unrecoverable += 1
            continue
        if (
            not recompute
            and existing_sha
            and existing_bytes is not None
            and existing_verified is not None
        ):
            already_done += 1
            continue
        resolved = _resolve(fp)
        if not resolved.exists():
            stale.append(
                StalePlan(
                    record_id=str(record_id),
                    document_id=str(document_id),
                    file_path=fp,
                    reason="missing_at_backfill",
                )
            )
            continue
        sha, size = _digest(resolved)
        computed.append(
            BackfillPlan(
                record_id=str(record_id),
                document_id=str(document_id),
                export_type=str(export_type),
                file_path=fp,
                sha256=sha,
                byte_count=size,
            )
        )
    return BackfillReport(
        computed=computed,
        stale=stale,
        already_done=already_done,
        skipped_unrecoverable=skipped_unrecoverable,
    )


def _apply(session, report: BackfillReport) -> None:
    now = datetime.now(timezone.utc)
    for plan in report.computed:
        session.execute(
            text(
                "UPDATE exports SET "
                "  content_sha256 = :sha, "
                "  byte_count = :bytes, "
                "  last_verified_at = :now "
                "WHERE id = :id"
            ),
            {"sha": plan.sha256, "bytes": plan.byte_count, "now": now, "id": plan.record_id},
        )
    for sp in report.stale:
        session.execute(
            text(
                "UPDATE exports SET stale_reason = COALESCE(stale_reason, :r) "
                "WHERE id = :id"
            ),
            {"r": sp.reason, "id": sp.record_id},
        )
    session.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Mutate the DB. Default is dry-run.")
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="Re-hash rows that already have content_sha256 populated.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = build_engine(str(settings.database_url))
    session_factory = build_session_factory(engine=engine)

    with session_factory() as session:
        report = _scan(session, recompute=args.recompute)
        print(f"already_done          : {report.already_done}")
        print(f"skipped_unrecoverable : {report.skipped_unrecoverable}")
        print(f"computed              : {len(report.computed)}")
        print(f"stale (file missing)  : {len(report.stale)}")
        for plan in report.computed:
            print(
                f"  HASH id={plan.record_id} type={plan.export_type}\n"
                f"       sha256={plan.sha256}  bytes={plan.byte_count}\n"
                f"       path={plan.file_path}"
            )
        for sp in report.stale:
            print(
                f"  STALE id={sp.record_id} doc={sp.document_id}\n"
                f"        path={sp.file_path}  reason={sp.reason}"
            )
        if args.apply and (report.computed or report.stale):
            _apply(session, report)
            print(
                f"\napplied {len(report.computed)} hash(es), "
                f"{len(report.stale)} stale-mark(s)."
            )
        elif not args.apply:
            print("\ndry-run (no mutations). pass --apply to write.")
    return 0 if not report.stale else 2


if __name__ == "__main__":
    raise SystemExit(main())
