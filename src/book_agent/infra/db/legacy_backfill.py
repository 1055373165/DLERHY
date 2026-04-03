from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine.url import make_url

logger = logging.getLogger(__name__)

_DEFAULT_SCAN_ROOT = "artifacts/real-book-live"
_SKIP_PATH_TOKENS = ("smoke", "debug", "sample")
_SKIP_TABLES = {"alembic_version"}


@dataclass(frozen=True, slots=True)
class LegacyDatabaseCandidate:
    source_db_path: Path
    document_id: str
    updated_at: str
    merged_export_count: int
    chapter_export_count: int
    succeeded_export_count: int


def backfill_legacy_history(database_url: str, *, scan_root: Path | None = None) -> int:
    target_db_path = _sqlite_database_path(database_url)
    if target_db_path is None or not target_db_path.exists():
        return 0

    effective_scan_root = (scan_root or target_db_path.parent / "real-book-live").resolve()
    if not effective_scan_root.exists():
        fallback_root = (Path.cwd() / _DEFAULT_SCAN_ROOT).resolve()
        effective_scan_root = fallback_root if fallback_root.exists() else effective_scan_root
    if not effective_scan_root.exists():
        return 0

    try:
        candidates = _discover_candidates(effective_scan_root, target_db_path)
        if not candidates:
            return 0
        existing_document_ids = _load_existing_document_ids(target_db_path)
        selected = _select_best_candidates(candidates, existing_document_ids)
        imported_count = 0
        for candidate in selected:
            _import_legacy_database(target_db_path, candidate.source_db_path)
            imported_count += 1
        if imported_count:
            logger.info(
                "Backfilled %s legacy analysis record(s) into %s from %s",
                imported_count,
                target_db_path,
                effective_scan_root,
            )
        return imported_count
    except Exception:
        logger.exception("Legacy history backfill failed for %s", target_db_path)
        return 0


def _sqlite_database_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return Path(database).expanduser().resolve()


def _discover_candidates(scan_root: Path, target_db_path: Path) -> list[LegacyDatabaseCandidate]:
    candidates: list[LegacyDatabaseCandidate] = []
    for source_db_path in sorted(scan_root.rglob("full.sqlite")):
        resolved = source_db_path.resolve()
        if resolved == target_db_path:
            continue
        if any(token in part.lower() for token in _SKIP_PATH_TOKENS for part in resolved.parts):
            continue
        candidates.extend(_load_candidates_from_database(resolved))
    return candidates


def _load_candidates_from_database(source_db_path: Path) -> list[LegacyDatabaseCandidate]:
    with closing(sqlite3.connect(f"file:{source_db_path}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if not _has_required_tables(connection, {"documents", "exports"}):
            return []
        rows = _fetchall(
            connection,
            """
            SELECT
              d.id AS document_id,
              COALESCE(d.updated_at, '') AS updated_at,
              COALESCE(SUM(CASE WHEN e.status = 'succeeded' AND e.export_type = 'merged_html' THEN 1 ELSE 0 END), 0)
                AS merged_export_count,
              COALESCE(SUM(CASE WHEN e.status = 'succeeded' AND e.export_type = 'bilingual_html' THEN 1 ELSE 0 END), 0)
                AS chapter_export_count,
              COALESCE(SUM(CASE WHEN e.status = 'succeeded' THEN 1 ELSE 0 END), 0)
                AS succeeded_export_count
            FROM documents AS d
            LEFT JOIN exports AS e
              ON e.document_id = d.id
            GROUP BY d.id, d.updated_at
            """,
        )
        return [
            LegacyDatabaseCandidate(
                source_db_path=source_db_path,
                document_id=str(row["document_id"]),
                updated_at=str(row["updated_at"] or ""),
                merged_export_count=int(row["merged_export_count"] or 0),
                chapter_export_count=int(row["chapter_export_count"] or 0),
                succeeded_export_count=int(row["succeeded_export_count"] or 0),
            )
            for row in rows
        ]


def _has_required_tables(connection: sqlite3.Connection, table_names: set[str]) -> bool:
    rows = _fetchall(
        connection,
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
    )
    available = {str(row[0]) for row in rows}
    return table_names.issubset(available)


def _load_existing_document_ids(target_db_path: Path) -> set[str]:
    with closing(sqlite3.connect(target_db_path)) as connection:
        rows = _fetchall(connection, "SELECT id FROM documents")
        return {str(row[0]) for row in rows}


def _select_best_candidates(
    candidates: list[LegacyDatabaseCandidate],
    existing_document_ids: set[str],
) -> list[LegacyDatabaseCandidate]:
    best_by_document_id: dict[str, LegacyDatabaseCandidate] = {}
    for candidate in candidates:
        if candidate.document_id in existing_document_ids:
            continue
        current = best_by_document_id.get(candidate.document_id)
        if current is None or _candidate_rank(candidate) > _candidate_rank(current):
            best_by_document_id[candidate.document_id] = candidate
    return sorted(best_by_document_id.values(), key=lambda candidate: candidate.source_db_path.as_posix())


def _candidate_rank(candidate: LegacyDatabaseCandidate) -> tuple[int, int, int, str]:
    return (
        candidate.merged_export_count,
        candidate.chapter_export_count,
        candidate.succeeded_export_count,
        candidate.updated_at,
    )


def _import_legacy_database(target_db_path: Path, source_db_path: Path) -> None:
    with closing(sqlite3.connect(target_db_path)) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("ATTACH DATABASE ? AS legacy", (str(source_db_path),))
        try:
            for table_name in _shared_table_names(connection):
                common_columns = _common_columns(connection, table_name)
                if not common_columns:
                    continue
                quoted_columns = ", ".join(_quote_identifier(column_name) for column_name in common_columns)
                connection.execute(
                    f"""
                    INSERT OR IGNORE INTO main.{_quote_identifier(table_name)} ({quoted_columns})
                    SELECT {quoted_columns}
                    FROM legacy.{_quote_identifier(table_name)}
                    """
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("DETACH DATABASE legacy")
            connection.execute("PRAGMA foreign_keys=ON")


def _shared_table_names(connection: sqlite3.Connection) -> list[str]:
    main_rows = _fetchall(
        connection,
        "SELECT name FROM main.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
    )
    legacy_rows = _fetchall(
        connection,
        "SELECT name FROM legacy.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
    )
    main_tables = {str(row[0]) for row in main_rows}
    legacy_tables = {str(row[0]) for row in legacy_rows}
    return sorted((main_tables & legacy_tables) - _SKIP_TABLES)


def _common_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    main_rows = _fetchall(connection, f"PRAGMA main.table_info({_quote_string(table_name)})")
    legacy_rows = _fetchall(connection, f"PRAGMA legacy.table_info({_quote_string(table_name)})")
    legacy_columns = {str(row[1]) for row in legacy_rows}
    return [str(row[1]) for row in main_rows if str(row[1]) in legacy_columns]


def _fetchall(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[object, ...] = (),
) -> list[sqlite3.Row | tuple]:
    cursor = connection.execute(query, parameters)
    try:
        return cursor.fetchall()
    finally:
        cursor.close()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
