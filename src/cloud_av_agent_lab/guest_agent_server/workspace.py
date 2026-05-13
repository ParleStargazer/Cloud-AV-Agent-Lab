from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_. -]+$")
LOGGER = logging.getLogger(__name__)


class WorkspaceError(ValueError):
    """Raised when a workspace request is unsafe or malformed."""


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when a prepared case workspace does not exist yet."""


@dataclass(frozen=True)
class FileProbe:
    exists: bool
    stat_ok: bool | None
    size: int | None = None
    error: str = ""
    probe_kind: str = "presence"


def safe_case_id(raw_case_id: object) -> str:
    case_id = str(raw_case_id or "").strip()
    if not case_id:
        raise WorkspaceError("case.id is required")
    if not SAFE_CASE_ID.fullmatch(case_id):
        raise WorkspaceError("case.id contains unsafe characters")
    if case_id in {".", ".."}:
        raise WorkspaceError("case.id cannot be a relative path segment")
    return case_id


def prepare_case_workspace(
    workdir: str | Path,
    payload: Mapping[str, Any],
) -> tuple[str, Path]:
    case_value = payload.get("case")
    if not isinstance(case_value, Mapping):
        raise WorkspaceError("prepare-case payload must include a case object")

    case_id = safe_case_id(case_value.get("id"))
    root = Path(workdir).resolve()
    cases_root = root / "cases"
    workspace = (cases_root / case_id).resolve()
    if not _is_relative_to(workspace, cases_root.resolve()):
        raise WorkspaceError("case workspace escapes the configured workdir")

    cases_root.mkdir(parents=True, exist_ok=True)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=False)
    (workspace / "case.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    sample_id = _payload_sample_id(payload)
    state = _base_case_state(
        case_id=case_id,
        sample_id=sample_id,
        phase="prepared",
        upload_state="not_uploaded",
        sample={
            "saved_once": False,
            "metadata_saved": False,
            "post_write_exists": False,
            "removed_after_save": False,
            "locked_or_busy": False,
            "stable": False,
            "size": None,
            "original_filename": "",
        },
    )
    write_case_state(workspace, state)
    append_event(
        workspace,
        event_type="case_prepared",
        case_id=case_id,
        sample_id=sample_id,
        message="case workspace prepared",
        data={"workspace": str(workspace)},
    )
    return case_id, workspace


def save_uploaded_sample(
    workdir: str | Path,
    case_id: str,
    content: bytes,
    sample_id: str,
    sha256: str,
    original_filename: str,
) -> tuple[Path, dict[str, Any]]:
    safe_id = safe_case_id(case_id)
    workspace = _case_workspace(workdir, safe_id)
    if not workspace.is_dir():
        raise WorkspaceNotFoundError(
            "case workspace does not exist; run guest-prepare-case first"
        )

    sample_dir = (workspace / "sample").resolve()
    if not _is_relative_to(sample_dir, workspace):
        raise WorkspaceError("sample directory escapes the case workspace")
    sample_dir.mkdir(parents=True, exist_ok=True)

    safe_name = safe_original_filename(original_filename)
    sample_path = (sample_dir / safe_name).resolve()
    if not _is_relative_to(sample_path, sample_dir):
        raise WorkspaceError("sample path escapes the sample directory")

    append_event(
        workspace,
        event_type="sample_upload_received",
        case_id=safe_id,
        sample_id=sample_id,
        message="sample upload request received",
        data={"original_filename": safe_name, "declared_sha256": sha256},
    )

    sample_path.write_bytes(content)
    LOGGER.info(
        "sample upload saved once: case_id=%s sample_id=%s size=%d "
        "original_filename=%s",
        safe_id,
        sample_id,
        len(content),
        safe_name,
    )
    append_event(
        workspace,
        event_type="sample_saved",
        case_id=safe_id,
        sample_id=sample_id,
        message="sample bytes saved once",
        data={"size": len(content), "original_filename": safe_name},
    )

    metadata = {
        "case_id": safe_id,
        "sample_id": str(sample_id),
        "sha256": str(sha256),
        "size": len(content),
        "original_filename": safe_name,
        "stored_filename": safe_name,
        "stored_at_utc": _utc_now(),
        "saved_once": True,
        "metadata_saved": True,
        "upload_state": "uploaded",
        "post_write_exists": True,
        "removed_after_save": False,
        "locked_or_busy": False,
        "stable": False,
    }
    (sample_dir / "sample.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    state = _base_case_state(
        case_id=safe_id,
        sample_id=sample_id,
        phase="uploaded",
        upload_state="uploaded",
        sample={
            "saved_once": True,
            "metadata_saved": True,
            "post_write_exists": True,
            "removed_after_save": False,
            "locked_or_busy": False,
            "stable": False,
            "size": len(content),
            "original_filename": safe_name,
            "sha256": str(sha256),
        },
    )
    write_case_state(workspace, state)
    return sample_path, metadata


def read_case_status(
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

    state = _read_json_file(workspace / "case_state.json")
    sample_metadata = _read_json_file(workspace / "sample" / "sample.json")
    if sample_metadata.get("saved_once") is True:
        sample_metadata, state = refresh_sample_status(
            workspace=workspace,
            state=state,
            sample_metadata=sample_metadata,
        )
    events = _read_recent_events(workspace / "events.jsonl", max_events=max_events)
    return {
        "case_id": safe_id,
        "workspace": str(workspace),
        "state": state,
        "sample_metadata": sample_metadata,
        "events": events,
    }


def refresh_sample_status(
    workspace: Path,
    state: Mapping[str, Any],
    sample_metadata: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = dict(sample_metadata)
    case_id = str(metadata.get("case_id") or state.get("case_id") or "")
    sample_id = str(metadata.get("sample_id") or state.get("sample_id") or "")
    stored_filename = safe_original_filename(
        metadata.get("stored_filename") or metadata.get("original_filename")
    )
    sample_path = (workspace / "sample" / stored_filename).resolve()
    if not _is_relative_to(sample_path, (workspace / "sample").resolve()):
        raise WorkspaceError("sample path escapes the sample directory")

    probe = _probe_sample_current_status(sample_path)
    expected_size = _coerce_int(metadata.get("size"))
    removed_after_save = not probe.exists
    locked_or_busy = probe.exists and probe.stat_ok is False
    stable = (
        probe.exists
        and probe.stat_ok is True
        and (expected_size is None or probe.size == expected_size)
    )

    if removed_after_save:
        upload_state = "removed_after_save"
        event_type = "sample_removed_after_save"
        message = "sample disappeared after being saved once"
    elif locked_or_busy:
        upload_state = "locked_or_busy"
        event_type = "sample_locked_or_busy"
        message = "sample exists but metadata is temporarily unavailable"
    elif stable:
        upload_state = "stable"
        event_type = "sample_stable_after_upload"
        message = "sample is present during status query"
    else:
        upload_state = "locked_or_busy"
        event_type = "sample_locked_or_busy"
        message = "sample metadata is inconsistent during status query"
        locked_or_busy = True

    status_fields = {
        "upload_state": upload_state,
        "post_write_exists": probe.exists,
        "removed_after_save": removed_after_save,
        "locked_or_busy": locked_or_busy,
        "stable": stable,
    }
    metadata.update(status_fields)
    (workspace / "sample" / "sample.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    updated_state = _base_case_state(
        case_id=case_id,
        sample_id=sample_id,
        phase="post_upload_checked",
        upload_state=upload_state,
        sample={
            "saved_once": True,
            "metadata_saved": True,
            "post_write_exists": probe.exists,
            "removed_after_save": removed_after_save,
            "locked_or_busy": locked_or_busy,
            "stable": stable,
            "size": expected_size,
            "current_size": probe.size,
            "original_filename": str(metadata.get("original_filename", "")),
            "sha256": str(metadata.get("sha256", "")),
        },
    )
    write_case_state(workspace, updated_state)

    append_event(
        workspace,
        event_type="sample_post_upload_check",
        case_id=case_id,
        sample_id=sample_id,
        message="sample status queried",
        data={
            "probe": probe.probe_kind,
            "exists": probe.exists,
            "stat_ok": probe.stat_ok,
            "size": probe.size,
            "error": probe.error,
        },
    )
    append_event(
        workspace,
        event_type=event_type,
        case_id=case_id,
        sample_id=sample_id,
        message=message,
        data=status_fields,
    )
    LOGGER.info(
        "sample status refreshed: case_id=%s sample_id=%s upload_state=%s "
        "exists=%s stat_ok=%s size=%s error=%s",
        case_id,
        sample_id,
        upload_state,
        probe.exists,
        probe.stat_ok,
        probe.size,
        probe.error,
    )
    return metadata, updated_state


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


def safe_original_filename(raw_filename: object) -> str:
    filename = Path(str(raw_filename or "sample.bin")).name.strip()
    if not filename:
        filename = "sample.bin"
    if filename in {".", ".."}:
        raise WorkspaceError("original filename cannot be a relative path segment")
    if not SAFE_FILENAME.fullmatch(filename):
        raise WorkspaceError("original filename contains unsafe characters")
    return filename


def _probe_sample_current_status(path: Path) -> FileProbe:
    if not path.exists():
        return FileProbe(exists=False, stat_ok=False, probe_kind="status")
    try:
        stat_result = path.stat()
    except (OSError, PermissionError) as exc:
        return FileProbe(
            exists=True,
            stat_ok=False,
            error=type(exc).__name__,
            probe_kind="status",
        )
    return FileProbe(
        exists=True,
        stat_ok=True,
        size=stat_result.st_size,
        probe_kind="status",
    )


def _case_workspace(workdir: str | Path, case_id: str) -> Path:
    root = Path(workdir).resolve()
    cases_root = (root / "cases").resolve()
    workspace = (cases_root / case_id).resolve()
    if not _is_relative_to(workspace, cases_root):
        raise WorkspaceError("case workspace escapes the configured workdir")
    return workspace


def _payload_sample_id(payload: Mapping[str, Any]) -> str:
    sample_value = payload.get("sample")
    if isinstance(sample_value, Mapping):
        return str(sample_value.get("id", ""))
    return ""


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


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True
