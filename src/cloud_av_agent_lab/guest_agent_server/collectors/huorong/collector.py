from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cloud_av_agent_lab.guest_agent_server.collectors.base import (
    CollectorArtifact,
    CollectionWindow,
    CollectorResult,
    NormalizedSecurityEvent,
    ProductLogCollector,
)
from cloud_av_agent_lab.guest_agent_server.workspace.io import _utc_now

from .parser import parse_huorong_row
from .schema import LOG_FILENAMES, PRODUCT_ID, TABLE_NAME, TABLE_NAME_PREFIX


class HuorongLogCollector(ProductLogCollector):
    product_id = PRODUCT_ID
    DEFAULT_LOG_DIR = Path(r"C:\ProgramData\Huorong\sysdiag")

    def __init__(self, log_dir: str | Path | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else self.DEFAULT_LOG_DIR

    def collect(
        self,
        workspace: Path,
        case_context: Mapping[str, Any],
        window: CollectionWindow,
    ) -> CollectorResult:
        artifact_dir = workspace / "collection" / PRODUCT_ID
        artifact_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        copied_files = self._copy_log_files(artifact_dir, errors)
        events: list[NormalizedSecurityEvent] = []
        db_copy = artifact_dir / "log.db"
        if db_copy.is_file():
            events.extend(
                self._read_events(
                    db_path=db_copy,
                    artifact_dir=artifact_dir,
                    case_context=case_context,
                    window=window,
                    errors=errors,
                )
            )
        else:
            errors.append("Huorong log.db was not found in the source log directory")

        verdict, intercepted, reason = _verdict_from_events_and_errors(events, errors)
        collection_state = (
            "collected" if not errors else "partial" if events else "failed"
        )
        return CollectorResult(
            product_id=PRODUCT_ID,
            collection_state=collection_state,
            verdict=verdict,
            intercepted=intercepted,
            reason=reason,
            evidence_count=len(events),
            events=tuple(events),
            errors=tuple(errors),
            artifacts={
                "artifact_dir": str(artifact_dir),
                "source_log_dir": str(self.log_dir),
                "copied_files": copied_files,
            },
            artifact_items=_huorong_artifacts(copied_files),
            window=window,
            collected_at_utc=_utc_now(),
        )

    def _copy_log_files(
        self,
        artifact_dir: Path,
        errors: list[str],
    ) -> list[dict[str, Any]]:
        copied_files: list[dict[str, Any]] = []
        for filename in LOG_FILENAMES:
            source = self.log_dir / filename
            destination = artifact_dir / filename
            metadata = {
                "name": filename,
                "source": str(source),
                "artifact": str(destination),
                "exists": source.is_file(),
                "size": None,
                "copied": False,
                "copied_at_utc": "",
            }
            if not source.is_file():
                copied_files.append(metadata)
                continue
            try:
                metadata["size"] = source.stat().st_size
                shutil.copy2(source, destination)
                metadata["copied"] = True
                metadata["copied_at_utc"] = _utc_now()
            except OSError as exc:
                errors.append(f"failed to copy {filename}: {type(exc).__name__}")
            copied_files.append(metadata)
        return copied_files

    def _read_events(
        self,
        db_path: Path,
        artifact_dir: Path,
        case_context: Mapping[str, Any],
        window: CollectionWindow,
        errors: list[str],
    ) -> list[NormalizedSecurityEvent]:
        events: list[NormalizedSecurityEvent] = []
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            table_name = _resolve_log_table(db)
            if not table_name:
                available_tables = _list_tables(db)
                errors.append(
                    "Huorong SQLite log table was not found; expected table "
                    f"prefix {TABLE_NAME_PREFIX!r}; available_tables="
                    f"{available_tables}"
                )
                return events
            columns = _table_columns(db, table_name)
            cursor = db.execute(f"SELECT * FROM {_quote_identifier(table_name)}")
            try:
                for row in cursor:
                    row_dict, row_error = _normalize_log_row(columns, tuple(row))
                    if row_error:
                        errors.append(row_error)
                        continue
                    raw_ref = f"{artifact_dir / 'log.db'}#{row_dict.get('id')}"
                    event, error = parse_huorong_row(
                        row_dict,
                        case_context,
                        window,
                        raw_ref=raw_ref,
                    )
                    if error:
                        errors.append(error)
                    if event is not None:
                        events.append(event)
            finally:
                cursor.close()
        except sqlite3.Error as exc:
            errors.append(
                "failed to read Huorong SQLite log: "
                f"{type(exc).__name__}: {_safe_sqlite_error_message(exc)}"
            )
        finally:
            if db is not None:
                db.close()
        return events


def _verdict_from_events_and_errors(
    events: list[NormalizedSecurityEvent],
    errors: list[str],
) -> tuple[str, bool | None, str]:
    if events:
        return "intercepted", True, "product_log_evidence_matched_case"
    if errors:
        return "unknown", None, "collection_failed_or_incomplete"
    return "not_intercepted", False, "no_product_log_evidence_in_window"


def _resolve_log_table(db: sqlite3.Connection) -> str:
    tables = _list_tables(db)
    if TABLE_NAME in tables:
        return TABLE_NAME
    candidates = [table for table in tables if table.startswith(TABLE_NAME_PREFIX)]
    if not candidates:
        return ""
    return sorted(candidates)[-1]


def _list_tables(db: sqlite3.Connection) -> list[str]:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows if row and _is_safe_identifier(str(row[0]))]


def _table_columns(db: sqlite3.Connection, table_name: str) -> list[str]:
    rows = db.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
    return [str(row[1]) for row in rows]


def _normalize_log_row(
    columns: list[str],
    row: tuple[Any, ...],
) -> tuple[dict[str, Any], str | None]:
    row_dict = dict(zip(columns, row, strict=False))
    json_column = _resolve_json_column(columns)
    if not json_column:
        return (
            row_dict,
            "Huorong log JSON column was not found; expected a known JSON "
            "column name or a fifth column containing JSON; columns="
            f"{columns}",
        )
    normalized = dict(row_dict)
    normalized["json_payload"] = row_dict.get(json_column)
    normalized["raw_json"] = row_dict.get(json_column)
    normalized["ts"] = _first_present(row_dict, "ts", "timestamp", "time")
    normalized["id"] = _first_present(row_dict, "id", "guid", "rowid")
    normalized["fid"] = _first_present(row_dict, "fid")
    normalized["fname"] = _first_present(row_dict, "fname")
    normalized["guid"] = _first_present(row_dict, "guid")
    normalized["_json_column"] = json_column
    normalized["_columns"] = columns
    return normalized, None


def _resolve_json_column(columns: list[str]) -> str:
    preferred_names = (
        "raw_json",
        "json",
        "payload",
        "data",
        "content",
        "detail",
        "event",
        "event_json",
        "log_json",
    )
    by_casefold = {column.casefold(): column for column in columns}
    for name in preferred_names:
        if name in by_casefold:
            return by_casefold[name]
    if len(columns) >= 5:
        return columns[4]
    return ""


def _first_present(row_dict: Mapping[str, Any], *names: str) -> Any:
    by_casefold = {key.casefold(): key for key in row_dict}
    for name in names:
        key = by_casefold.get(name.casefold())
        if key is not None:
            return row_dict.get(key)
    return None


def _quote_identifier(value: str) -> str:
    if not _is_safe_identifier(value):
        raise sqlite3.OperationalError("unsafe SQLite table identifier")
    return '"' + value.replace('"', '""') + '"'


def _is_safe_identifier(value: str) -> bool:
    return bool(value) and all(
        character.isalnum() or character == "_" for character in value
    )


def _safe_sqlite_error_message(exc: sqlite3.Error) -> str:
    message = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return message[:300]


def _huorong_artifacts(
    copied_files: list[dict[str, Any]],
) -> tuple[CollectorArtifact, ...]:
    items: list[CollectorArtifact] = []
    copied_by_name = {
        str(item.get("name", "")): bool(item.get("copied"))
        for item in copied_files
        if isinstance(item, dict)
    }
    for filename in LOG_FILENAMES:
        items.append(
            CollectorArtifact(
                path=f"collection/{PRODUCT_ID}/{filename}",
                category="raw_product_log",
                include_in_evidence=False,
                redaction_owner="collector",
                redaction_state="raw_blocked",
                sensitivity="high",
                reason=(
                    "raw Huorong SQLite artifact is guest-reported and is not "
                    "included in the default redacted evidence bundle"
                    if copied_by_name.get(filename)
                    else "raw Huorong SQLite artifact was not copied"
                ),
            )
        )
    items.append(
        CollectorArtifact(
            path="collector/normalized_evidence.json",
            category="normalized_evidence",
            include_in_evidence=True,
            redaction_owner="exporter",
            redaction_state="redacted",
            sensitivity="low",
            reason="derived normalized product evidence with exporter text redaction",
        )
    )
    return tuple(items)
