from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import WorkspaceNotFoundError
from .io import _coerce_int, _read_json_file, _read_recent_events
from .paths import _case_workspace, safe_case_id


def read_case_report(
    workdir: str | Path,
    case_id: str,
    max_events: int = 20,
) -> dict[str, Any]:
    safe_id = safe_case_id(case_id)
    workspace = _case_workspace(workdir, safe_id)
    if not workspace.is_dir():
        raise WorkspaceNotFoundError(
            "case workspace does not exist; run guest-prepare-case first"
        )
    return write_case_report(workspace, max_events=max_events)


def write_case_report(workspace: Path, max_events: int = 20) -> dict[str, Any]:
    report = _build_case_report(workspace, max_events=max_events)
    (workspace / "case_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def _build_case_report(workspace: Path, max_events: int) -> dict[str, Any]:
    case_data = _read_json_file(workspace / "case.json")
    state = _read_json_file(workspace / "case_state.json")
    sample_metadata = _read_json_file(workspace / "sample" / "sample.json")
    collection_data = _read_json_file(workspace / "case_collection.json")
    all_events = _read_recent_events(workspace / "events.jsonl", max_events=1000)
    recent_events = all_events[-max(0, max_events) :]

    case_id = str(state.get("case_id") or _mapping_value(case_data, "case", "id"))
    sample_id = str(
        state.get("sample_id")
        or sample_metadata.get("sample_id")
        or _mapping_value(case_data, "sample", "id")
    )
    sample_state = state.get("sample")
    sample_state = sample_state if isinstance(sample_state, Mapping) else {}
    execution_state = state.get("execution")
    execution_state = execution_state if isinstance(execution_state, Mapping) else {}

    report = {
        "case_id": case_id,
        "sample_id": sample_id,
        "vm_id": str(_mapping_value(case_data, "vm", "id")),
        "product_id": str(_mapping_value(case_data, "product", "id")),
        "upload_state": str(state.get("upload_state", "not_uploaded")),
        "saved_once": _bool_field(sample_state, sample_metadata, "saved_once"),
        "post_write_exists": _bool_field(
            sample_state,
            sample_metadata,
            "post_write_exists",
        ),
        "removed_after_save": _bool_field(
            sample_state,
            sample_metadata,
            "removed_after_save",
        ),
        "locked_or_busy": _bool_field(sample_state, sample_metadata, "locked_or_busy"),
        "stable": _bool_field(sample_state, sample_metadata, "stable"),
        "original_filename": str(
            sample_state.get("original_filename")
            or sample_metadata.get("original_filename", "")
        ),
        "sha256": str(
            sample_state.get("sha256")
            or sample_metadata.get("sha256")
            or _mapping_value(case_data, "sample", "sha256")
        ),
        "size": _coerce_int(sample_state.get("size") or sample_metadata.get("size")),
        "prepared_at_utc": _first_event_time(all_events, "case_prepared"),
        "uploaded_at_utc": str(
            sample_metadata.get("stored_at_utc")
            or _first_event_time(all_events, "sample_saved")
        ),
        "updated_at_utc": str(state.get("updated_at_utc", "")),
        "execution": {
            "enabled": bool(execution_state.get("enabled", False)),
            "requested": bool(execution_state.get("requested", False)),
            "state": str(execution_state.get("state", "not_started")),
            "root_pid": _coerce_int(
                execution_state.get("root_pid") or execution_state.get("pid")
            ),
            "exit_code": _coerce_int(execution_state.get("exit_code")),
            "children": list(execution_state.get("children", []))
            if isinstance(execution_state.get("children"), list)
            else [],
            "started_at_utc": str(execution_state.get("started_at_utc", "")),
            "last_observed_at_utc": str(
                execution_state.get("last_observed_at_utc", "")
            ),
            "observation_count": _coerce_int(execution_state.get("observation_count"))
            or 0,
            "sample_id": str(execution_state.get("sample_id", "")),
            "expected_sha256": str(execution_state.get("expected_sha256", "")),
            "sample_path_under_case": bool(
                execution_state.get("sample_path_under_case", False)
            ),
        },
        "collection": {
            "product_id": str(collection_data.get("product_id", "")),
            "state": str(collection_data.get("collection_state", "not_collected")),
            "verdict": str(collection_data.get("verdict", "unknown")),
            "intercepted": collection_data.get("intercepted"),
            "reason": str(collection_data.get("reason", "")),
            "evidence_count": _coerce_int(collection_data.get("evidence_count")) or 0,
            "collected_at_utc": str(collection_data.get("collected_at_utc", "")),
            "errors": list(collection_data.get("errors", []))
            if isinstance(collection_data.get("errors"), list)
            else [],
        },
        "recent_events": recent_events,
    }
    return report


def _mapping_value(
    payload: Mapping[str, Any],
    table_name: str,
    key: str,
) -> object:
    value = payload.get(table_name)
    if isinstance(value, Mapping):
        return value.get(key, "")
    return ""


def _bool_field(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any],
    key: str,
) -> bool:
    if key in primary:
        return bool(primary[key])
    return bool(fallback.get(key, False))


def _first_event_time(events: list[dict[str, Any]], event_type: str) -> str:
    for event in events:
        if event.get("event_type") == event_type:
            return str(event.get("timestamp_utc", ""))
    return ""
