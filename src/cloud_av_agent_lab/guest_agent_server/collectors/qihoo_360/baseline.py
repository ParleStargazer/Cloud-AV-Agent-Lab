from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import PRODUCT_ID, Qihoo360ParsedEvent, Qihoo360SummaryDatabase
from .sqlite_reader import read_qihoo360_summary_database


@dataclass(frozen=True)
class Qihoo360SummaryBaseline:
    source_path: str
    summary_dat_size: int
    summary_dat_mtime_utc: str
    max_fq_id: int | None
    known_fq_ids: Sequence[int]
    product_id: str = PRODUCT_ID
    schema_version: str = "qihoo360-summary-baseline.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "product_id": self.product_id,
            "summary_dat_size": self.summary_dat_size,
            "summary_dat_mtime_utc": self.summary_dat_mtime_utc,
            "max_fq_id": self.max_fq_id,
            "known_fq_ids": list(self.known_fq_ids),
            "source_path": self.source_path,
        }


@dataclass(frozen=True)
class Qihoo360DeltaFilter:
    baseline_max_fq_id: int | None
    current_max_fq_id: int | None
    candidate_fq_ids: Sequence[int]
    baseline_delta_usable: bool
    warnings: Sequence[str]
    product_id: str = PRODUCT_ID
    schema_version: str = "qihoo360-delta-filter.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "product_id": self.product_id,
            "baseline_max_fq_id": self.baseline_max_fq_id,
            "current_max_fq_id": self.current_max_fq_id,
            "candidate_fq_ids": list(self.candidate_fq_ids),
            "baseline_delta_usable": self.baseline_delta_usable,
            "warnings": list(self.warnings),
        }


def read_qihoo360_summary_baseline(path: str | Path) -> Qihoo360SummaryBaseline:
    database_path = Path(path)
    summary = read_qihoo360_summary_database(database_path)
    return build_qihoo360_summary_baseline(summary, database_path)


def build_qihoo360_summary_baseline(
    summary: Qihoo360SummaryDatabase,
    path: str | Path | None = None,
) -> Qihoo360SummaryBaseline:
    source_path = Path(path or summary.path)
    stat = source_path.stat()
    fq_ids = _fq_ids(summary.events)
    return Qihoo360SummaryBaseline(
        source_path=str(source_path),
        summary_dat_size=stat.st_size,
        summary_dat_mtime_utc=_format_utc(
            datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        ),
        max_fq_id=max(fq_ids) if fq_ids else None,
        known_fq_ids=tuple(fq_ids),
    )


def filter_qihoo360_delta_events(
    summary: Qihoo360SummaryDatabase,
    baseline: Qihoo360SummaryBaseline,
) -> tuple[tuple[Qihoo360ParsedEvent, ...], Qihoo360DeltaFilter]:
    current_ids = _fq_ids(summary.events)
    current_max = max(current_ids) if current_ids else None
    warnings: list[str] = []

    if (
        baseline.max_fq_id is not None
        and current_max is not None
        and current_max < baseline.max_fq_id
    ):
        warnings.append("summary_db_reset_or_rotated")
        delta = Qihoo360DeltaFilter(
            baseline_max_fq_id=baseline.max_fq_id,
            current_max_fq_id=current_max,
            candidate_fq_ids=(),
            baseline_delta_usable=False,
            warnings=tuple(warnings),
        )
        return (), delta

    threshold = baseline.max_fq_id or 0
    candidate_ids = tuple(
        record_id for record_id in current_ids if record_id > threshold
    )
    selected = tuple(
        event for event in summary.events if event.source_row_id in set(candidate_ids)
    )
    delta = Qihoo360DeltaFilter(
        baseline_max_fq_id=baseline.max_fq_id,
        current_max_fq_id=current_max,
        candidate_fq_ids=candidate_ids,
        baseline_delta_usable=True,
        warnings=tuple(warnings),
    )
    return selected, delta


def _fq_ids(events: Sequence[Qihoo360ParsedEvent]) -> list[int]:
    return sorted(
        event.source_row_id
        for event in events
        if event.source_row_id is not None and event.source_row_id >= 0
    )


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
