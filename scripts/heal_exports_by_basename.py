"""Heal ExportRecord rows whose file_path points at an unreachable location.

This is the M0 data-fix counterpart to the Phase-2 self-heal logic now
embedded in ``_resolve_artifact_path``: the route will heal on read,
but the DB row stays stale until something rewrites it. This script
does that rewrite deterministically for every row whose stored
``file_path`` is no longer reachable but whose basename is present
under the canonical ``<artifacts_root>/exports/<document_id>/`` layout.

Design choices worth noting:

* Dry-run is the default. Nothing mutates unless ``--apply`` is passed.
* The script never synthesizes new artifact content — it only rewrites
  ``file_path`` to an existing file on disk. Missing-and-unrecoverable
  rows are reported but left alone so an operator can decide whether
  to trigger a rebuild or drop the row.
* Rate-limiting, blob-reaper, and content-digest verification are
  intentionally deferred to M1/M2; this script is scoped to what M0
  promises: "fix the known poisoned records so downloads work today."
"""

from __future__ import annotations

import argparse
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


@dataclass(slots=True)
class HealPlan:
    record_id: str
    document_id: str
    export_type: str
    old_path: str
    new_path: str


@dataclass(slots=True)
class HealReport:
    fixable: list[HealPlan]
    already_ok: int
    unrecoverable: list[tuple[str, str, str]]  # (record_id, document_id, old_path)


def _candidate_paths(
    root: Path, document_id: str, basename: str
) -> list[Path]:
    return [
        root / document_id / basename,
        root / "exports" / document_id / basename,
    ]


def _scan(session, artifacts_root: Path) -> HealReport:
    rows = session.execute(
        text(
            "SELECT id, document_id, export_type, file_path "
            "FROM exports WHERE status = 'succeeded'"
        )
    ).all()
    fixable: list[HealPlan] = []
    already_ok = 0
    unrecoverable: list[tuple[str, str, str]] = []
    for record_id, document_id, export_type, file_path in rows:
        stored = Path(str(file_path))
        if not stored.is_absolute():
            stored = (ROOT / stored).resolve()
        if stored.exists():
            already_ok += 1
            continue
        basename = stored.name
        healed: Path | None = None
        for candidate in _candidate_paths(artifacts_root, str(document_id), basename):
            if candidate.exists():
                healed = candidate.resolve()
                break
        if healed is None:
            unrecoverable.append((str(record_id), str(document_id), str(file_path)))
            continue
        # Store paths relative to repo root when the original was also
        # relative; otherwise use absolute. The route resolves both.
        try:
            new_path = str(healed.relative_to(ROOT))
        except ValueError:
            new_path = str(healed)
        fixable.append(
            HealPlan(
                record_id=str(record_id),
                document_id=str(document_id),
                export_type=str(export_type),
                old_path=str(file_path),
                new_path=new_path,
            )
        )
    return HealReport(
        fixable=fixable, already_ok=already_ok, unrecoverable=unrecoverable
    )


def _apply(session, plans: list[HealPlan]) -> None:
    for plan in plans:
        session.execute(
            text("UPDATE exports SET file_path = :p WHERE id = :id"),
            {"p": plan.new_path, "id": plan.record_id},
        )
    session.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Mutate the DB. Default is dry-run.")
    parser.add_argument(
        "--artifacts-root",
        default=str(ROOT / "artifacts"),
        help="Canonical artifacts root under which to reverse-lookup basenames.",
    )
    args = parser.parse_args(argv)

    artifacts_root = Path(args.artifacts_root).resolve()
    settings = get_settings()
    engine = build_engine(str(settings.database_url))
    session_factory = build_session_factory(engine=engine)

    with session_factory() as session:
        report = _scan(session, artifacts_root)
        print(f"already_ok    : {report.already_ok}")
        print(f"fixable       : {len(report.fixable)}")
        print(f"unrecoverable : {len(report.unrecoverable)}")
        for plan in report.fixable:
            print(
                f"  FIX  id={plan.record_id}  type={plan.export_type}\n"
                f"       from {plan.old_path}\n"
                f"       to   {plan.new_path}"
            )
        for rid, did, old in report.unrecoverable:
            print(f"  MISS id={rid} doc={did} path={old}")
        if args.apply and report.fixable:
            _apply(session, report.fixable)
            print(f"\napplied {len(report.fixable)} UPDATE(s).")
        elif not args.apply:
            print("\ndry-run (no mutations). pass --apply to write.")
    return 0 if not report.unrecoverable else 2


if __name__ == "__main__":
    raise SystemExit(main())
