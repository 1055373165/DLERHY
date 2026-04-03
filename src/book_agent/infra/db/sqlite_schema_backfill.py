from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy.engine.url import make_url

from book_agent.domain.document_titles import resolve_document_titles
from book_agent.domain.enums import SourceType


def ensure_sqlite_schema_compat(database_url: str) -> int:
    database_path = _sqlite_database_path(database_url)
    if database_path is None or not database_path.exists():
        return 0

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.row_factory = sqlite3.Row
        if not _has_table(connection, "documents"):
            return 0

        added_column_count = 0
        document_columns = _table_columns(connection, "documents")
        for column_name in ("title_src", "title_tgt"):
            if column_name in document_columns:
                continue
            connection.execute(f'ALTER TABLE "documents" ADD COLUMN "{column_name}" TEXT')
            added_column_count += 1

        for table_name in ("document_runs", "work_items", "run_budgets"):
            if not _has_table(connection, table_name):
                continue
            table_columns = _table_columns(connection, table_name)
            if "runtime_bundle_revision_id" not in table_columns:
                connection.execute(
                    f'ALTER TABLE "{table_name}" ADD COLUMN "runtime_bundle_revision_id" TEXT'
                )
                added_column_count += 1
            connection.execute(
                "CREATE INDEX IF NOT EXISTS "
                f'"idx_{table_name}_runtime_bundle_revision" '
                f'ON "{table_name}"("runtime_bundle_revision_id")'
            )
        document_columns = _table_columns(connection, "documents")

        connection.execute(
            """
            UPDATE documents
            SET title_src = COALESCE(title_src, title)
            WHERE (title_src IS NULL OR TRIM(title_src) = '')
              AND title IS NOT NULL
              AND TRIM(title) <> ''
            """
        )
        _backfill_legacy_document_titles(connection, document_columns)
        connection.commit()
        return added_column_count


def _sqlite_database_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return Path(database).expanduser().resolve()


def _has_table(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return {str(row[1]) for row in rows}


def _backfill_legacy_document_titles(
    connection: sqlite3.Connection,
    document_columns: set[str],
) -> None:
    select_columns = [
        column_name
        for column_name in (
            "id",
            "title",
            "title_src",
            "title_tgt",
            "source_type",
            "source_path",
            "src_lang",
            "tgt_lang",
            "metadata_json",
        )
        if column_name in document_columns
    ]
    if not {"id", "title", "title_src", "title_tgt", "source_type", "source_path"}.issubset(select_columns):
        return

    rows = connection.execute(
        f"SELECT {', '.join(select_columns)} FROM documents"
    ).fetchall()
    for row in rows:
        source_type = _coerce_source_type(row["source_type"])
        if source_type is None:
            continue
        metadata = _parse_json_object(row["metadata_json"])
        pdf_profile = metadata.get("pdf_profile") if isinstance(metadata.get("pdf_profile"), dict) else {}
        parsed_title = _normalize_text(row["title_src"]) or _normalize_text(row["title"])
        resolved = resolve_document_titles(
            source_type=source_type,
            parsed_title=parsed_title,
            parsed_metadata=metadata,
            source_path=row["source_path"],
            src_lang=_normalize_text(row["src_lang"]),
            tgt_lang=_normalize_text(row["tgt_lang"]),
            pdf_recovery_lane=_normalize_text(pdf_profile.get("recovery_lane")),
        )

        current_title = _normalize_text(row["title"])
        current_title_src = _normalize_text(row["title_src"])
        current_title_tgt = _normalize_text(row["title_tgt"])
        target_title = resolved.title or current_title
        target_title_src = resolved.title_src or current_title_src or current_title
        target_title_tgt = current_title_tgt or resolved.title_tgt
        if (
            target_title == current_title
            and target_title_src == current_title_src
            and target_title_tgt == current_title_tgt
        ):
            continue

        metadata["document_title"] = {
            "title": target_title,
            "src": target_title_src,
            "tgt": target_title_tgt,
            "resolution_source": resolved.resolution_source,
        }
        connection.execute(
            """
            UPDATE documents
            SET title = ?, title_src = ?, title_tgt = ?, metadata_json = ?
            WHERE id = ?
            """,
            (
                target_title,
                target_title_src,
                target_title_tgt,
                json.dumps(metadata, ensure_ascii=False),
                row["id"],
            ),
        )


def _coerce_source_type(value: object) -> SourceType | None:
    normalized = _normalize_text(value)
    if normalized is None:
        return None
    try:
        return SourceType(normalized)
    except ValueError:
        return None


def _parse_json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
