from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import WorkspaceError


def append_event(
    workspace: Path,
    event_type: str,
    case_id: str,
    sample_id: str,
    message: str,
    data: Mapping[str, Any] | None = None,
) -> None:
    event = {
        "timestamp_utc": _utc_now(),
        "event_type": event_type,
        "case_id": str(case_id),
        "sample_id": str(sample_id),
        "message": message,
        "data": dict(data or {}),
    }
    with (workspace / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


def write_case_state(workspace: Path, state: Mapping[str, Any]) -> None:
    (workspace / "case_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _case_sample_id(workspace: Path) -> str:
    state = _read_json_file(workspace / "case_state.json")
    sample_metadata = _read_json_file(workspace / "sample" / "sample.json")
    return str(sample_metadata.get("sample_id") or state.get("sample_id") or "")


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _base_case_state(
    case_id: str,
    sample_id: str,
    phase: str,
    upload_state: str,
    sample: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": str(case_id),
        "sample_id": str(sample_id),
        "phase": phase,
        "upload_state": upload_state,
        "sample": dict(sample),
        "updated_at_utc": _utc_now(),
    }


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkspaceError(f"{path.name} is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise WorkspaceError(f"{path.name} must contain a JSON object")
    return decoded


def _read_recent_events(path: Path, max_events: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if max_events <= 0:
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events: list[dict[str, Any]] = []
    for line in lines[-max(0, max_events) :]:
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkspaceError("events.jsonl contains invalid JSON") from exc
        if isinstance(decoded, dict):
            events.append(decoded)
    return events


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
