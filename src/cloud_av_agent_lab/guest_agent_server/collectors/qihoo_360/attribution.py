from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cloud_av_agent_lab.guest_agent_server.collectors.base import CollectionWindow

from .schema import Qihoo360ParsedEvent

EICAR_SHA256 = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"


@dataclass(frozen=True)
class Qihoo360Attribution:
    level: str
    matched_on: Sequence[str]
    warnings: Sequence[str]
    path_matched: bool
    sha256_matched: bool
    baseline_delta_matched: bool
    time_window_matched: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "matched_on": list(self.matched_on),
            "warnings": list(self.warnings),
            "path_matched": self.path_matched,
            "sha256_matched": self.sha256_matched,
            "baseline_delta_matched": self.baseline_delta_matched,
            "time_window_matched": self.time_window_matched,
        }


def attribute_qihoo360_event(
    event: Qihoo360ParsedEvent,
    case_context: Mapping[str, Any],
    window: CollectionWindow | None = None,
    baseline_delta_ids: Sequence[int] = (),
) -> Qihoo360Attribution:
    matched_on: list[str] = []
    warnings: list[str] = []
    path_matched = _path_matches(event.file_path, case_context)
    sha256_matched = _sha256_matches(event.sha256, case_context)
    baseline_delta_matched = (
        event.source_row_id is not None
        and event.source_row_id in set(baseline_delta_ids)
    )
    time_window_matched = _timestamp_in_window(event.observed_at_utc, window)
    has_product_evidence = bool(event.threat_name or event.quarantine_path)

    if path_matched:
        matched_on.append("case_sample_path")
    if sha256_matched:
        matched_on.append("sha256")
    if baseline_delta_matched:
        matched_on.append("baseline_delta")
    if time_window_matched:
        matched_on.append("time_window")
    if event.threat_name:
        matched_on.append("threat_name")

    if sha256_matched and _is_eicar_hash(event.sha256) and not path_matched:
        warnings.extend(["eicar_hash_is_reused_across_cases", "case_path_not_matched"])

    if path_matched and has_product_evidence:
        level = "strong"
    elif baseline_delta_matched and path_matched:
        level = "strong"
    elif baseline_delta_matched and sha256_matched and not _is_eicar_hash(event.sha256):
        level = "medium"
    elif sha256_matched:
        level = "medium"
    elif time_window_matched and event.threat_name:
        level = "weak"
    else:
        level = "unattributed"

    return Qihoo360Attribution(
        level=level,
        matched_on=tuple(dict.fromkeys(matched_on)),
        warnings=tuple(dict.fromkeys(warnings)),
        path_matched=path_matched,
        sha256_matched=sha256_matched,
        baseline_delta_matched=baseline_delta_matched,
        time_window_matched=time_window_matched,
    )


def _path_matches(path: str, case_context: Mapping[str, Any]) -> bool:
    haystack = _normalize_path(path)
    if not haystack:
        return False
    sample_dir = _normalize_path(str(case_context.get("sample_dir") or ""))
    if sample_dir and (
        haystack == sample_dir or haystack.startswith(sample_dir + "\\")
    ):
        return True
    case_id = _normalize_path(str(case_context.get("case_id") or ""))
    filenames = [
        _normalize_path(str(case_context.get("stored_filename") or "")),
        _normalize_path(str(case_context.get("original_filename") or "")),
    ]
    return bool(
        case_id
        and case_id in haystack
        and any(
            filename and haystack.endswith("\\" + filename) for filename in filenames
        )
    )


def _sha256_matches(value: str, case_context: Mapping[str, Any]) -> bool:
    event_hash = value.strip().casefold()
    sample_hash = str(case_context.get("sample_sha256") or "").strip().casefold()
    return bool(event_hash and sample_hash and event_hash == sample_hash)


def _is_eicar_hash(value: str) -> bool:
    return value.strip().casefold() == EICAR_SHA256


def _timestamp_in_window(
    timestamp_utc: str,
    window: CollectionWindow | None,
) -> bool:
    if window is None:
        return False
    timestamp = _parse_utc(timestamp_utc)
    start = _parse_utc(window.start_utc)
    end = _parse_utc(window.end_utc)
    if timestamp is None or start is None or end is None:
        return False
    return start <= timestamp <= end


def _parse_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_path(value: str) -> str:
    normalized = value.replace("/", "\\").casefold().rstrip("\\")
    for prefix in ("file:_", "file:", "\\\\?\\"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    return normalized
