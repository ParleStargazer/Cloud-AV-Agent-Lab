from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from cloud_av_agent_lab.guest_agent_server.collectors.base import (
    CollectionWindow,
    NormalizedSecurityEvent,
)

from .schema import (
    BLOCK_KEYWORDS,
    DELETE_KEYWORDS,
    DETECTION_FIELDS,
    HASH_FIELDS,
    PATH_FIELDS,
    PID_FIELDS,
    PRODUCT_ID,
    PRODUCT_LOG_SOURCE,
    QUARANTINE_KEYWORDS,
)


def parse_huorong_row(
    row: Mapping[str, Any],
    case_context: Mapping[str, Any],
    window: CollectionWindow,
    raw_ref: str,
) -> tuple[NormalizedSecurityEvent | None, str | None]:
    timestamp_utc = _timestamp_to_utc(row.get("ts"))
    if timestamp_utc and not _timestamp_in_window(timestamp_utc, window):
        return None, None

    json_payload_text = str(_row_value(row, "json_payload", "raw_json", "detail") or "")
    try:
        raw_payload = json.loads(json_payload_text) if json_payload_text else {}
    except json.JSONDecodeError as exc:
        return (
            None,
            "Huorong JSON payload parse failed for row "
            f"{_row_value(row, 'id', 'guid')}: {exc.msg}",
        )

    if not isinstance(raw_payload, Mapping):
        return (
            None,
            "Huorong JSON payload must be an object for row "
            f"{_row_value(row, 'id', 'guid')}",
        )
    detail_value = raw_payload.get("detail", {})
    version_value = raw_payload.get("version", {})
    detail = detail_value if isinstance(detail_value, Mapping) else {}
    version = version_value if isinstance(version_value, Mapping) else {}

    hash_match = _hash_matches(detail, case_context)
    path_match = _path_matches(detail, case_context)
    pid_match = _pid_matches(detail, case_context)
    has_detection_signal = _has_detection_signal(detail)
    if not (hash_match or path_match or pid_match):
        return None, None
    if not has_detection_signal:
        return None, None

    confidence = "high" if hash_match else "medium" if path_match else "low"
    event_type = _event_type(detail)
    recname = _field_text(detail, "recname")
    message = (
        recname or _field_text(detail, "description") or "Huorong log matched case"
    )
    evidence = _selected_evidence(
        row=row,
        detail=detail,
        version=version,
        hash_match=hash_match,
        path_match=path_match,
        pid_match=pid_match,
    )
    return (
        NormalizedSecurityEvent(
            timestamp_utc=timestamp_utc or str(window.collection_started_at_utc),
            source=PRODUCT_LOG_SOURCE,
            event_type=event_type,
            case_id=str(case_context.get("case_id", "")),
            sample_id=str(case_context.get("sample_id", "")),
            product_id=PRODUCT_ID,
            confidence=confidence,
            message=message,
            evidence=evidence,
            raw_ref=raw_ref,
        ),
        None,
    )


def _hash_matches(
    detail: Mapping[str, Any],
    case_context: Mapping[str, Any],
) -> bool:
    expected_sha256 = str(case_context.get("sample_sha256") or "").casefold()
    if not expected_sha256:
        return False
    for field_name in HASH_FIELDS:
        observed = str(detail.get(field_name) or "").casefold()
        if observed and observed == expected_sha256:
            return True
    return False


def _path_matches(
    detail: Mapping[str, Any],
    case_context: Mapping[str, Any],
) -> bool:
    needles = [
        str(case_context.get("sample_dir") or ""),
        str(case_context.get("case_id") or ""),
        str(case_context.get("stored_filename") or ""),
        str(case_context.get("original_filename") or ""),
    ]
    normalized_needles = [_normalize_path_text(value) for value in needles if value]
    if not normalized_needles:
        return False
    for field_name in PATH_FIELDS:
        haystack = _normalize_path_text(str(detail.get(field_name) or ""))
        if haystack and any(needle in haystack for needle in normalized_needles):
            return True
    return False


def _pid_matches(
    detail: Mapping[str, Any],
    case_context: Mapping[str, Any],
) -> bool:
    pids = set()
    root_pid = case_context.get("root_pid")
    if root_pid is not None:
        pids.add(str(root_pid))
    for child_pid in case_context.get("child_pids", []):
        pids.add(str(child_pid))
    if not pids:
        return False
    return any(str(detail.get(field_name) or "") in pids for field_name in PID_FIELDS)


def _row_value(row: Mapping[str, Any], *names: str) -> Any:
    by_casefold = {str(key).casefold(): key for key in row}
    for name in names:
        key = by_casefold.get(name.casefold())
        if key is not None:
            return row.get(key)
    return None


def _has_detection_signal(detail: Mapping[str, Any]) -> bool:
    return any(
        str(detail.get(field_name) or "").strip() for field_name in DETECTION_FIELDS
    )


def _event_type(detail: Mapping[str, Any]) -> str:
    action_text = " ".join(
        str(detail.get(field_name) or "")
        for field_name in ("action", "treatment", "result", "description")
    ).casefold()
    if any(keyword.casefold() in action_text for keyword in QUARANTINE_KEYWORDS):
        return "av_quarantined"
    if any(keyword.casefold() in action_text for keyword in DELETE_KEYWORDS):
        return "av_deleted"
    if any(keyword.casefold() in action_text for keyword in BLOCK_KEYWORDS):
        return "av_blocked"
    return "av_detected"


def _selected_evidence(
    row: Mapping[str, Any],
    detail: Mapping[str, Any],
    version: Mapping[str, Any],
    hash_match: bool,
    path_match: bool,
    pid_match: bool,
) -> dict[str, Any]:
    selected_detail_keys = sorted(
        set(DETECTION_FIELDS + HASH_FIELDS + PATH_FIELDS + PID_FIELDS)
    )
    detail_evidence = {
        key: str(detail[key])
        for key in selected_detail_keys
        if key in detail and detail[key] not in (None, "")
    }
    version_evidence = {
        key: str(version[key])
        for key in ("product", "dbtime")
        if key in version and version[key] not in (None, "")
    }
    return {
        "row_id": _row_value(row, "id", "guid"),
        "fname": _row_value(row, "fname"),
        "guid": _row_value(row, "guid"),
        "json_column": _row_value(row, "_json_column"),
        "hash_match": hash_match,
        "path_match": path_match,
        "pid_match": pid_match,
        "detail": detail_evidence,
        "version": version_evidence,
    }


def _field_text(detail: Mapping[str, Any], field_name: str) -> str:
    return str(detail.get(field_name) or "").strip()


def _normalize_path_text(value: str) -> str:
    return value.replace("/", "\\").casefold()


def _timestamp_to_utc(value: object) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    if timestamp > 10_000_000_000:
        timestamp = timestamp // 1000
    try:
        return (
            datetime.fromtimestamp(timestamp, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OSError, OverflowError, ValueError):
        return ""


def _timestamp_in_window(timestamp_utc: str, window: CollectionWindow) -> bool:
    timestamp = _parse_utc(timestamp_utc)
    start = _parse_utc(window.start_utc)
    end = _parse_utc(window.end_utc)
    if timestamp is None or start is None or end is None:
        return True
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
