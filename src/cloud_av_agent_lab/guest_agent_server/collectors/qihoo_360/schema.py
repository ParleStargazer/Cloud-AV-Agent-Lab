from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

PRODUCT_ID = "qihoo-360"
PRODUCT_LOG_SOURCE = "product_log"
RAW_PRODUCT_NAME = "360safe"

SUMMARY_DATABASE_NAME = "360safe.Summary.dat"
UNION_METADATA_NAME = "360safe.Summary.union1"

KNOWN_FIELD_LABELS: Mapping[str, str] = {
    "@200": "event_source",
    "@201": "raw_product",
    "@203": "threat_name",
    "@204": "threat_category",
    "@205": "raw_action_text",
    "@206": "event_time_raw",
    "@208": "related_path",
    "@209": "related_path",
    "@500": "file_path",
    "@501": "file_size",
    "@502": "quarantine_path",
    "@510": "md5",
    "@512": "sha1",
    "@513": "sha256",
    "@514": "raw_category_hint",
}


@dataclass(frozen=True)
class Qihoo360FileIndexRecord:
    record_id: int
    original_path: str = ""
    quarantine_path: str = ""
    md5: str = ""
    sha1: str = ""
    sha256: str = ""
    file_size: int | None = None
    version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "original_path": self.original_path,
            "quarantine_path": self.quarantine_path,
            "md5": self.md5,
            "sha1": self.sha1,
            "sha256": self.sha256,
            "file_size": self.file_size,
            "version": self.version,
        }


@dataclass(frozen=True)
class Qihoo360ParsedEvent:
    source_row_id: int | None = None
    event_source: str = ""
    raw_product: str = ""
    threat_name: str = ""
    threat_category: str = ""
    raw_action_text: str = ""
    event_time_raw: str = ""
    observed_at_utc: str = ""
    time_confidence: str = "unknown"
    related_paths: Sequence[str] = field(default_factory=tuple)
    file_path: str = ""
    file_size: int | None = None
    quarantine_path: str = ""
    md5: str = ""
    sha1: str = ""
    sha256: str = ""
    raw_category_hint: str = ""
    raw_fields: Mapping[str, Any] = field(default_factory=dict)
    unknown_fields: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    parse_warnings: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_row_id": self.source_row_id,
            "event_source": self.event_source,
            "raw_product": self.raw_product,
            "threat_name": self.threat_name,
            "threat_category": self.threat_category,
            "raw_action_text": self.raw_action_text,
            "event_time_raw": self.event_time_raw,
            "observed_at_utc": self.observed_at_utc,
            "time_confidence": self.time_confidence,
            "related_paths": list(self.related_paths),
            "file_path": self.file_path,
            "file_size": self.file_size,
            "quarantine_path": self.quarantine_path,
            "md5": self.md5,
            "sha1": self.sha1,
            "sha256": self.sha256,
            "raw_category_hint": self.raw_category_hint,
            "raw_fields": dict(self.raw_fields),
            "unknown_fields": {
                key: dict(value) for key, value in self.unknown_fields.items()
            },
            "parse_warnings": list(self.parse_warnings),
        }


@dataclass(frozen=True)
class Qihoo360SummaryDatabase:
    path: str
    table_names: Sequence[str]
    file_index: Sequence[Qihoo360FileIndexRecord]
    events: Sequence[Qihoo360ParsedEvent]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "table_names": list(self.table_names),
            "file_index": [record.to_dict() for record in self.file_index],
            "events": [event.to_dict() for event in self.events],
        }
