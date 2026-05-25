from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from .parser import decode_qihoo360_text, parse_qihoo360_fc_blob
from .schema import (
    Qihoo360FileIndexRecord,
    Qihoo360ParsedEvent,
    Qihoo360SummaryDatabase,
)

SQLITE_HEADER = b"SQLite format 3\x00"
REQUIRED_TABLES = ("CF", "FI", "FQ")


class Qihoo360SQLiteError(RuntimeError):
    """Raised when a 360safe Summary.dat snapshot cannot be parsed."""


def validate_qihoo360_summary_header(path: str | Path) -> None:
    try:
        header = Path(path).read_bytes()[: len(SQLITE_HEADER)]
    except OSError as exc:
        raise Qihoo360SQLiteError(f"failed to read Summary.dat header: {exc}") from exc
    if header != SQLITE_HEADER:
        raise Qihoo360SQLiteError("360safe.Summary.dat is not a SQLite database")


def read_qihoo360_summary_database(path: str | Path) -> Qihoo360SummaryDatabase:
    database_path = Path(path)
    validate_qihoo360_summary_header(database_path)
    connection = _connect_readonly(database_path)
    try:
        table_names = _table_names(connection)
        _validate_schema(table_names)
        file_index = _read_file_index(connection)
        events = _read_fq_events(connection, file_index)
    finally:
        connection.close()
    return Qihoo360SummaryDatabase(
        path=str(database_path),
        table_names=tuple(table_names),
        file_index=tuple(file_index.values()),
        events=tuple(events),
    )


def _connect_readonly(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro", uri=True
        )
    except sqlite3.Error as exc:
        raise Qihoo360SQLiteError(f"failed to open Summary.dat SQLite: {exc}") from exc
    connection.text_factory = bytes
    connection.row_factory = sqlite3.Row
    return connection


def _table_names(connection: sqlite3.Connection) -> list[str]:
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    except sqlite3.Error as exc:
        raise Qihoo360SQLiteError(f"failed to list Summary.dat tables: {exc}") from exc
    return [decode_qihoo360_text(row[0]) for row in rows]


def _validate_schema(table_names: list[str]) -> None:
    missing = sorted(set(REQUIRED_TABLES) - set(table_names))
    if missing:
        raise Qihoo360SQLiteError(
            "360safe.Summary.dat missing required tables: " + ", ".join(missing)
        )


def _read_file_index(
    connection: sqlite3.Connection,
) -> dict[int, Qihoo360FileIndexRecord]:
    try:
        rows = connection.execute('SELECT * FROM "FI" ORDER BY "ID"').fetchall()
    except sqlite3.Error as exc:
        raise Qihoo360SQLiteError(f"failed to read FI table: {exc}") from exc
    records: dict[int, Qihoo360FileIndexRecord] = {}
    for row in rows:
        record_id = _row_int(row, "ID")
        if record_id is None:
            continue
        records[record_id] = Qihoo360FileIndexRecord(
            record_id=record_id,
            original_path=_row_text(row, "FO"),
            quarantine_path=_row_text(row, "FQ"),
            md5=_row_text(row, "M5").casefold(),
            sha1=_row_text(row, "S1").casefold(),
            sha256=_row_text(row, "S6").casefold(),
            file_size=_row_int(row, "SZ"),
            version=_row_int(row, "VR"),
        )
    return records


def _read_fq_events(
    connection: sqlite3.Connection,
    file_index: Mapping[int, Qihoo360FileIndexRecord],
) -> list[Qihoo360ParsedEvent]:
    try:
        rows = connection.execute('SELECT * FROM "FQ" ORDER BY "ID"').fetchall()
    except sqlite3.Error as exc:
        raise Qihoo360SQLiteError(f"failed to read FQ table: {exc}") from exc
    events: list[Qihoo360ParsedEvent] = []
    for row in rows:
        record_id = _row_int(row, "ID")
        blob = row["FC"] if "FC" in row.keys() else b""
        if not isinstance(blob, bytes):
            blob = bytes(blob or b"")
        parsed = parse_qihoo360_fc_blob(blob, source_row_id=record_id)
        if record_id is not None:
            parsed = _merge_file_index(parsed, file_index.get(record_id))
        events.append(parsed)
    return events


def _merge_file_index(
    event: Qihoo360ParsedEvent,
    file_record: Qihoo360FileIndexRecord | None,
) -> Qihoo360ParsedEvent:
    if file_record is None:
        return event
    return replace(
        event,
        file_path=event.file_path or file_record.original_path,
        quarantine_path=event.quarantine_path or file_record.quarantine_path,
        md5=event.md5 or file_record.md5,
        sha1=event.sha1 or file_record.sha1,
        sha256=event.sha256 or file_record.sha256,
        file_size=event.file_size
        if event.file_size is not None
        else file_record.file_size,
    )


def _row_text(row: sqlite3.Row, key: str) -> str:
    if key not in row.keys():
        return ""
    return decode_qihoo360_text(row[key]).strip()


def _row_int(row: sqlite3.Row, key: str) -> int | None:
    if key not in row.keys():
        return None
    value = row[key]
    try:
        return int(value)
    except (TypeError, ValueError):
        text = decode_qihoo360_text(value).strip()
        try:
            return int(text)
        except ValueError:
            return None
