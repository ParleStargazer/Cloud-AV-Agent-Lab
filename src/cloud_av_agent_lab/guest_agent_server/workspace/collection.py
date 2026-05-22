from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cloud_av_agent_lab.guest_agent_server.collectors.base import (
    CollectionWindow,
    CollectorResult,
    NormalizedSecurityEvent,
    ProductLogCollector,
)
from cloud_av_agent_lab.guest_agent_server.collectors.registry import (
    get_product_log_collector,
    supported_product_log_collectors,
)

from .errors import WorkspaceError, WorkspaceNotFoundError
from .io import (
    _coerce_int,
    _read_json_file,
    _read_recent_events,
    _utc_now,
    append_event,
    write_case_state,
)
from .paths import _case_workspace, safe_case_id
from .reports import write_case_report

WINDOW_PRE_BUFFER_SECONDS = 60


def collect_case_logs(
    workdir: str | Path,
    case_id: str,
    product_id: str,
    max_events: int = 20,
) -> dict[str, Any]:
    safe_id = safe_case_id(case_id)
    safe_product = safe_case_id(product_id)
    workspace = _case_workspace(workdir, safe_id)
    if not workspace.is_dir():
        raise WorkspaceNotFoundError(
            "case workspace does not exist; run guest-prepare-case first"
        )
    collector = _collector_for(safe_product)

    case_data = _read_json_file(workspace / "case.json")
    state = _read_json_file(workspace / "case_state.json")
    sample_metadata = _read_json_file(workspace / "sample" / "sample.json")
    events = _read_recent_events(workspace / "events.jsonl", max_events=1000)
    window = _build_collection_window(state, sample_metadata, events)
    context = _build_case_context(
        workspace=workspace,
        case_data=case_data,
        state=state,
        sample_metadata=sample_metadata,
    )
    context["product_id"] = safe_product

    append_event(
        workspace,
        event_type="collection_started",
        case_id=safe_id,
        sample_id=str(context.get("sample_id", "")),
        message="product log collection started",
        data={"product_id": safe_product, "window": window.to_dict()},
    )
    result = collector.collect(workspace, context, window)
    append_event(
        workspace,
        event_type="collection_finished",
        case_id=safe_id,
        sample_id=str(context.get("sample_id", "")),
        message="product log collection finished",
        data={
            "product_id": safe_product,
            "collection_state": result.collection_state,
            "verdict": result.verdict,
            "evidence_count": result.evidence_count,
            "errors_count": len(result.errors),
        },
    )
    _update_case_state_collection(workspace, state, result)
    payload = write_case_collection(workspace, result, max_events=max_events)
    write_case_report(workspace)
    return payload


def read_case_collection_status(
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
    collection_file = workspace / "case_collection.json"
    if not collection_file.is_file():
        return {
            "case_id": safe_id,
            "collection_state": "not_collected",
            "message": "collection has not been run for this case",
            "recent_events": _read_recent_events(
                workspace / "events.jsonl",
                max_events=max_events,
            ),
        }
    payload = _read_json_file(collection_file)
    payload["recent_events"] = _read_recent_events(
        workspace / "events.jsonl",
        max_events=max_events,
    )
    return payload


def write_case_collection(
    workspace: Path,
    result: CollectorResult,
    max_events: int = 20,
) -> dict[str, Any]:
    result_data = result.to_dict()
    state = _read_json_file(workspace / "case_state.json")
    sample_metadata = _read_json_file(workspace / "sample" / "sample.json")
    events = _read_recent_events(workspace / "events.jsonl", max_events=1000)
    payload = {
        "case_id": str(state.get("case_id", "")),
        "sample_id": str(
            sample_metadata.get("sample_id") or state.get("sample_id", "")
        ),
        "product_id": result.product_id,
        "collection_state": result.collection_state,
        "verdict": result.verdict,
        "intercepted": result.intercepted,
        "reason": result.reason,
        "evidence_count": result.evidence_count,
        "collected_at_utc": result.collected_at_utc,
        "window": result_data.get("window", {}),
        "artifacts": result_data.get("artifacts", {}),
        "errors": result_data.get("errors", []),
        "events": result_data.get("events", []),
        "timeline": _build_timeline(events, result.events),
        "recent_events": _read_recent_events(
            workspace / "events.jsonl",
            max_events=max_events,
        ),
    }
    (workspace / "case_collection.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _collector_for(product_id: str) -> ProductLogCollector:
    collector = get_product_log_collector(product_id)
    if collector is not None:
        return collector
    available = ", ".join(supported_product_log_collectors()) or "none"
    raise WorkspaceError(
        f"collector for product {product_id!r} is not supported; "
        f"available collectors: {available}"
    )


def _build_case_context(
    workspace: Path,
    case_data: Mapping[str, Any],
    state: Mapping[str, Any],
    sample_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    sample_dir = (workspace / "sample").resolve()
    execution = state.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    children = execution.get("children", [])
    child_pids = []
    if isinstance(children, list):
        child_pids = [
            child.get("pid")
            for child in children
            if isinstance(child, Mapping) and child.get("pid") is not None
        ]
    return {
        "case_id": str(state.get("case_id") or _mapping_value(case_data, "case", "id")),
        "sample_id": str(
            sample_metadata.get("sample_id")
            or state.get("sample_id")
            or _mapping_value(case_data, "sample", "id")
        ),
        "sample_sha256": str(
            sample_metadata.get("sha256")
            or _mapping_value(case_data, "sample", "sha256")
        ),
        "sample_dir": str(sample_dir),
        "stored_filename": str(sample_metadata.get("stored_filename", "")),
        "original_filename": str(sample_metadata.get("original_filename", "")),
        "root_pid": _coerce_int(execution.get("root_pid") or execution.get("pid")),
        "child_pids": child_pids,
        "vm_id": str(_mapping_value(case_data, "vm", "id")),
        "case_workspace": str(workspace),
    }


def _build_collection_window(
    state: Mapping[str, Any],
    sample_metadata: Mapping[str, Any],
    events: list[dict[str, Any]],
) -> CollectionWindow:
    collection_started = _utc_now()
    prepared_at = _first_event_time(events, "case_prepared")
    uploaded_at = str(
        sample_metadata.get("stored_at_utc")
        or _first_event_time(events, "sample_saved")
    )
    execution = state.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    execution_started = str(
        execution.get("started_at_utc")
        or _first_event_time(events, "execution_started")
    )
    anchor = (
        _parse_utc(uploaded_at)
        or _parse_utc(prepared_at)
        or _parse_utc(collection_started)
    )
    end = _parse_utc(collection_started) or datetime.now(timezone.utc)
    start = anchor - timedelta(seconds=WINDOW_PRE_BUFFER_SECONDS)
    return CollectionWindow(
        start_utc=_format_utc(start),
        end_utc=_format_utc(end),
        case_prepared_at_utc=prepared_at,
        uploaded_at_utc=uploaded_at,
        execution_started_at_utc=execution_started,
        collection_started_at_utc=collection_started,
        collection_finished_at_utc=collection_started,
    )


def _build_timeline(
    guest_events: list[dict[str, Any]],
    product_events: Any,
) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for event in guest_events:
        event_type = str(event.get("event_type", ""))
        timeline.append(
            {
                "timestamp_utc": str(event.get("timestamp_utc", "")),
                "source": _timeline_source(event_type),
                "event_type": event_type,
                "case_id": str(event.get("case_id", "")),
                "sample_id": str(event.get("sample_id", "")),
                "product_id": str(_event_data(event).get("product_id", "")),
                "confidence": "high",
                "message": str(event.get("message", "")),
                "evidence": _event_data(event),
                "raw_ref": "events.jsonl",
            }
        )
    for event in product_events:
        if isinstance(event, NormalizedSecurityEvent):
            timeline.append(event.to_dict())
    return sorted(timeline, key=lambda item: str(item.get("timestamp_utc", "")))


def _update_case_state_collection(
    workspace: Path,
    state: Mapping[str, Any],
    result: CollectorResult,
) -> None:
    updated_state = dict(state)
    updated_state["phase"] = "collection_collected"
    updated_state["collection"] = {
        "product_id": result.product_id,
        "state": result.collection_state,
        "verdict": result.verdict,
        "intercepted": result.intercepted,
        "reason": result.reason,
        "evidence_count": result.evidence_count,
        "collected_at_utc": result.collected_at_utc,
    }
    updated_state["updated_at_utc"] = _utc_now()
    write_case_state(workspace, updated_state)


def _mapping_value(
    payload: Mapping[str, Any],
    table_name: str,
    key: str,
) -> object:
    value = payload.get(table_name)
    if isinstance(value, Mapping):
        return value.get(key, "")
    return ""


def _first_event_time(events: list[dict[str, Any]], event_type: str) -> str:
    for event in events:
        if event.get("event_type") == event_type:
            return str(event.get("timestamp_utc", ""))
    return ""


def _event_data(event: Mapping[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    return dict(data) if isinstance(data, Mapping) else {}


def _timeline_source(event_type: str) -> str:
    if event_type.startswith("sample_"):
        return "upload_observer"
    if event_type.startswith("execution_"):
        return "execution_observer"
    return "guest_agent"


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


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
