from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_. -]+$")
ALLOWED_CASE_ACTIONS = {
    "generate_report",
    "observe_case",
    "dry_run_execute_uploaded_sample",
    "execute_uploaded_sample",
}
FORBIDDEN_ACTION_FIELDS = {
    "args",
    "arguments",
    "cmd",
    "command",
    "exec",
    "executable",
    "file",
    "file_path",
    "path",
    "powershell",
    "run_command",
    "sample_path",
    "shell",
}
LOGGER = logging.getLogger(__name__)
TERMINAL_EXECUTION_STATES = {
    "exited_cleanly",
    "exited_with_error",
    "launch_failed",
    "terminated_or_disappeared",
}


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


@dataclass
class ExecutionRegistry:
    """In-memory handles for processes started by this Guest Agent instance."""

    processes: dict[str, Any] = field(default_factory=dict)

    def register(self, case_id: str, process: Any) -> None:
        self.processes[case_id] = process

    def get(self, case_id: str) -> Any | None:
        return self.processes.get(case_id)

    def remove(self, case_id: str) -> None:
        self.processes.pop(case_id, None)


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
    write_case_report(workspace)
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
    write_case_report(workspace)
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


def read_case_execution_status(
    workdir: str | Path,
    case_id: str,
    execution_registry: ExecutionRegistry | None = None,
    max_events: int = 20,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    safe_id = safe_case_id(case_id)
    workspace = _case_workspace(workdir, safe_id)
    if not workspace.is_dir():
        raise WorkspaceNotFoundError(
            "case workspace does not exist; run guest-prepare-case first"
        )

    state = _read_json_file(workspace / "case_state.json")
    sample_id = _case_sample_id(workspace)
    execution = state.get("execution")
    if not isinstance(execution, Mapping) or (
        not execution.get("requested") and not execution.get("root_pid")
    ):
        events = _read_recent_events(workspace / "events.jsonl", max_events=max_events)
        return {
            "case_id": safe_id,
            "sample_id": sample_id,
            "execution_state": "not_started",
            "root_pid": None,
            "exit_code": None,
            "children": [],
            "observed_at_utc": _utc_now(),
            "recent_events": events,
        }

    observed = _observe_execution(
        workspace=workspace,
        case_id=safe_id,
        sample_id=sample_id,
        state=state,
        execution_registry=execution_registry,
        timeout_seconds=timeout_seconds,
    )
    events = _read_recent_events(workspace / "events.jsonl", max_events=max_events)
    return {
        "case_id": safe_id,
        "sample_id": sample_id,
        "execution_state": observed["state"],
        "root_pid": observed.get("root_pid"),
        "exit_code": observed.get("exit_code"),
        "children": observed.get("children", []),
        "observed_at_utc": observed["last_observed_at_utc"],
        "recent_events": events,
        "execution": observed,
    }


def write_case_report(workspace: Path, max_events: int = 20) -> dict[str, Any]:
    report = _build_case_report(workspace, max_events=max_events)
    (workspace / "case_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def run_case_action(
    workdir: str | Path,
    case_id: str,
    payload: Mapping[str, Any],
    execution_enabled: bool = False,
    execution_registry: ExecutionRegistry | None = None,
) -> dict[str, Any]:
    safe_id = safe_case_id(case_id)
    workspace = _case_workspace(workdir, safe_id)
    if not workspace.is_dir():
        raise WorkspaceNotFoundError(
            "case workspace does not exist; run guest-prepare-case first"
        )
    if not isinstance(payload, Mapping):
        raise WorkspaceError("case action payload must be a JSON object")

    forbidden_fields = sorted(_find_forbidden_action_fields(payload))
    if forbidden_fields:
        raise WorkspaceError(
            "case action payload contains forbidden execution fields: "
            + ", ".join(forbidden_fields)
        )

    action = str(payload.get("action", "")).strip()
    if action not in ALLOWED_CASE_ACTIONS:
        raise WorkspaceError(
            "case action is not allowed; allowed actions are: "
            + ", ".join(sorted(ALLOWED_CASE_ACTIONS))
        )

    sample_id = _case_sample_id(workspace)
    if action == "generate_report":
        append_event(
            workspace,
            event_type="case_report_generated",
            case_id=safe_id,
            sample_id=sample_id,
            message="case delivery report generated",
            data={"action": action},
        )
        report = write_case_report(workspace)
        return {
            "action": action,
            "action_state": "report_generated",
            "message": "case report generated",
            "report": report,
        }

    if action == "observe_case":
        status = read_case_status(workdir, safe_id)
        return {
            "action": action,
            "action_state": "case_observed",
            "message": "case status observed",
            "status": status,
        }

    if action == "execute_uploaded_sample":
        if not execution_enabled:
            append_event(
                workspace,
                event_type="execution_disabled",
                case_id=safe_id,
                sample_id=sample_id,
                message="real sample execution is disabled",
                data={"action": action, "execution_enabled": execution_enabled},
            )
            write_case_report(workspace)
            return {
                "action": action,
                "execution_state": "execution_disabled",
                "message": "execution is disabled; no sample was executed",
            }
        return _execute_uploaded_sample(
            workspace,
            safe_id,
            payload,
            execution_registry=execution_registry,
        )

    return _dry_run_execute_uploaded_sample(workspace, safe_id, payload)


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
    existing_execution = state.get("execution")
    if isinstance(existing_execution, Mapping):
        updated_state["execution"] = dict(existing_execution)
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
    write_case_report(workspace)
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


def _build_case_report(workspace: Path, max_events: int) -> dict[str, Any]:
    case_data = _read_json_file(workspace / "case.json")
    state = _read_json_file(workspace / "case_state.json")
    sample_metadata = _read_json_file(workspace / "sample" / "sample.json")
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
        "recent_events": recent_events,
    }
    return report


def _dry_run_execute_uploaded_sample(
    workspace: Path,
    case_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    context = _uploaded_sample_execution_context(workspace, payload)
    probe = _probe_sample_current_status(context["sample_path"])
    append_event(
        workspace,
        event_type="execution_dry_run_checked",
        case_id=case_id,
        sample_id=context["sample_id"],
        message="dry-run execution metadata checked; no process was started",
        data={
            "expected_sha256_match": context["expected_sha256_match"],
            "sample_present": probe.exists,
            "sample_stat_ok": probe.stat_ok,
            "sample_path_under_case": True,
        },
    )
    write_case_report(workspace)
    return {
        "action": "dry_run_execute_uploaded_sample",
        "execution_state": "execution_dry_run_checked",
        "message": "dry-run checked uploaded sample metadata; no sample was executed",
        "sample_id": context["sample_id"],
        "expected_sha256_match": context["expected_sha256_match"],
        "sample_present": probe.exists,
        "sample_stat_ok": probe.stat_ok,
        "sample_path_under_case": True,
    }


def _execute_uploaded_sample(
    workspace: Path,
    case_id: str,
    payload: Mapping[str, Any],
    execution_registry: ExecutionRegistry | None = None,
) -> dict[str, Any]:
    context = _uploaded_sample_execution_context(workspace, payload)
    sample_path = context["sample_path"]
    sample_dir = context["sample_dir"]
    expected_sha256 = str(payload.get("expected_sha256", "")).strip()
    requested_at = _utc_now()
    append_event(
        workspace,
        event_type="execution_requested",
        case_id=case_id,
        sample_id=context["sample_id"],
        message="controlled execution requested for registered uploaded sample",
        data={
            "sample_path_under_case": True,
            "expected_sha256_match": context["expected_sha256_match"],
        },
    )
    if not os.path.exists(sample_path):
        append_event(
            workspace,
            event_type="sample_missing_before_execution",
            case_id=case_id,
            sample_id=context["sample_id"],
            message="uploaded sample was missing before execution",
            data={"sample_path_under_case": True},
        )
        write_case_report(workspace)
        raise WorkspaceError("uploaded sample is missing before execution")

    try:
        process = subprocess.Popen(  # noqa: S603
            [str(sample_path)],
            cwd=str(sample_dir),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_no_window_creationflags(),
            close_fds=True,
        )
    except OSError as exc:
        state = dict(context["state"])
        execution = {
            "enabled": True,
            "requested": True,
            "state": "launch_failed",
            "root_pid": None,
            "pid": None,
            "sample_id": context["sample_id"],
            "expected_sha256": expected_sha256,
            "sample_path_under_case": True,
            "started_at_utc": "",
            "last_observed_at_utc": requested_at,
            "exit_code": None,
            "children": [],
            "observation_count": 0,
            "error": type(exc).__name__,
        }
        state["phase"] = "execution_observed"
        state["execution"] = execution
        state["updated_at_utc"] = requested_at
        write_case_state(workspace, state)
        append_event(
            workspace,
            event_type="execution_launch_failed",
            case_id=case_id,
            sample_id=context["sample_id"],
            message="uploaded sample failed to start",
            data={"error": type(exc).__name__, "sample_path_under_case": True},
        )
        write_case_report(workspace)
        raise WorkspaceError(
            f"uploaded sample failed to start: {type(exc).__name__}"
        ) from exc

    started_at = _utc_now()
    if execution_registry is not None:
        execution_registry.register(case_id, process)
    state = dict(context["state"])
    state["phase"] = "execution_started"
    state["execution"] = {
        "enabled": True,
        "requested": True,
        "state": "running",
        "root_pid": process.pid,
        "pid": process.pid,
        "sample_id": context["sample_id"],
        "expected_sha256": expected_sha256,
        "expected_sha256_match": context["expected_sha256_match"],
        "stored_filename": sample_path.name,
        "cwd": str(sample_dir),
        "sample_path_under_case": True,
        "started_at_utc": started_at,
        "last_observed_at_utc": started_at,
        "exit_code": None,
        "children": [],
        "observation_count": 0,
    }
    state["updated_at_utc"] = started_at
    write_case_state(workspace, state)
    append_event(
        workspace,
        event_type="execution_started",
        case_id=case_id,
        sample_id=context["sample_id"],
        message="uploaded sample process started",
        data={
            "pid": process.pid,
            "started_at_utc": started_at,
            "sample_path_under_case": True,
        },
    )
    write_case_report(workspace)
    return {
        "action": "execute_uploaded_sample",
        "execution_state": "running",
        "message": "uploaded sample process started",
        "root_pid": process.pid,
        "pid": process.pid,
        "sample_id": context["sample_id"],
        "expected_sha256": expected_sha256,
        "started_at_utc": started_at,
        "sample_path_under_case": True,
    }


def _uploaded_sample_execution_context(
    workspace: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    state = _read_json_file(workspace / "case_state.json")
    sample_metadata = _read_json_file(workspace / "sample" / "sample.json")
    if sample_metadata.get("saved_once") is not True:
        raise WorkspaceError(
            "execute_uploaded_sample requires a previously uploaded sample"
        )

    sample_id = str(sample_metadata.get("sample_id") or state.get("sample_id") or "")
    requested_sample_id = str(payload.get("sample_id", "")).strip()
    if requested_sample_id and requested_sample_id != sample_id:
        raise WorkspaceError("sample_id does not match the prepared case metadata")

    expected_sha256 = str(payload.get("expected_sha256", "")).strip()
    recorded_sha256 = str(sample_metadata.get("sha256", "")).strip()
    if expected_sha256 and recorded_sha256 and expected_sha256 != recorded_sha256:
        raise WorkspaceError("expected_sha256 does not match uploaded sample metadata")

    stored_filename = safe_original_filename(
        sample_metadata.get("stored_filename")
        or sample_metadata.get("original_filename")
    )
    sample_dir = (workspace / "sample").resolve()
    sample_path = (sample_dir / stored_filename).resolve()
    if not _is_relative_to(sample_path, sample_dir):
        raise WorkspaceError("sample path escapes the sample directory")
    return {
        "state": state,
        "sample_metadata": sample_metadata,
        "sample_id": sample_id,
        "sample_dir": sample_dir,
        "sample_path": sample_path,
        "expected_sha256_match": not expected_sha256
        or not recorded_sha256
        or expected_sha256 == recorded_sha256,
    }


def _observe_execution(
    workspace: Path,
    case_id: str,
    sample_id: str,
    state: Mapping[str, Any],
    execution_registry: ExecutionRegistry | None,
    timeout_seconds: float | None,
) -> dict[str, Any]:
    previous_execution = state.get("execution")
    execution = (
        dict(previous_execution) if isinstance(previous_execution, Mapping) else {}
    )
    root_pid = _coerce_int(execution.get("root_pid") or execution.get("pid"))
    observed_at = _utc_now()
    previous_state = str(execution.get("state", "unknown"))
    process = execution_registry.get(case_id) if execution_registry else None
    registry_exit_code = _poll_registered_process(process)
    recorded_exit_code = _coerce_int(execution.get("exit_code"))
    exit_code = (
        registry_exit_code if registry_exit_code is not None else recorded_exit_code
    )

    snapshot = _snapshot_process_tree(root_pid) if root_pid is not None else {}
    children = list(snapshot.get("children", []))
    children_running = any(child.get("status") == "running" for child in children)
    root_observable = bool(snapshot.get("root_exists", False))
    psutil_available = bool(snapshot.get("available", False))
    registered_running = process is not None and registry_exit_code is None

    if previous_state == "launch_failed":
        execution_state = "launch_failed"
    elif exit_code is not None and not children_running:
        execution_state = "exited_cleanly" if exit_code == 0 else "exited_with_error"
    elif registered_running or root_observable or children_running:
        execution_state = "running"
        exit_code = None
    elif psutil_available:
        execution_state = "terminated_or_disappeared"
    else:
        execution_state = "unknown"

    if (
        execution_state == "running"
        and timeout_seconds is not None
        and _elapsed_seconds(execution.get("started_at_utc"), observed_at)
        >= timeout_seconds
    ):
        execution_state = "timeout_still_running"

    observation_count = (_coerce_int(execution.get("observation_count")) or 0) + 1
    execution.update(
        {
            "state": execution_state,
            "root_pid": root_pid,
            "pid": root_pid,
            "exit_code": exit_code,
            "children": children,
            "last_observed_at_utc": observed_at,
            "observation_count": observation_count,
            "low_intrusion_observation": True,
        }
    )
    updated_state = dict(state)
    updated_state["phase"] = "execution_observed"
    updated_state["execution"] = execution
    updated_state["updated_at_utc"] = observed_at
    write_case_state(workspace, updated_state)

    append_event(
        workspace,
        event_type="execution_observed",
        case_id=case_id,
        sample_id=sample_id,
        message="execution process tree observed with low-intrusion metadata query",
        data={
            "root_pid": root_pid,
            "execution_state": execution_state,
            "exit_code": exit_code,
            "children_count": len(children),
            "psutil_available": psutil_available,
            "low_intrusion_observation": True,
        },
    )
    if children:
        append_event(
            workspace,
            event_type="execution_child_observed",
            case_id=case_id,
            sample_id=sample_id,
            message="child processes observed for the case root pid",
            data={"root_pid": root_pid, "children_count": len(children)},
        )
    if execution_state in {"exited_cleanly", "exited_with_error"}:
        _append_once_for_observation_state(
            workspace,
            previous_state=previous_state,
            current_state=execution_state,
            event_type="execution_exited",
            case_id=case_id,
            sample_id=sample_id,
            message="root process exited; no AV verdict is inferred",
            data={"root_pid": root_pid, "exit_code": exit_code},
        )
    elif execution_state == "timeout_still_running":
        _append_once_for_observation_state(
            workspace,
            previous_state=previous_state,
            current_state=execution_state,
            event_type="execution_timeout_still_running",
            case_id=case_id,
            sample_id=sample_id,
            message="polling window ended while process tree was still running",
            data={"root_pid": root_pid, "children_count": len(children)},
        )
    elif execution_state == "terminated_or_disappeared":
        _append_once_for_observation_state(
            workspace,
            previous_state=previous_state,
            current_state=execution_state,
            event_type="execution_recorded",
            case_id=case_id,
            sample_id=sample_id,
            message=(
                "process is no longer observable; no AV verdict is inferred from "
                "this observation alone"
            ),
            data={"root_pid": root_pid},
        )

    if (
        execution_state in TERMINAL_EXECUTION_STATES
        or execution_state == "timeout_still_running"
    ) and execution_registry is not None:
        execution_registry.remove(case_id)
    write_case_report(workspace)
    return execution


def _poll_registered_process(process: Any | None) -> int | None:
    if process is None:
        return None
    poll = getattr(process, "poll", None)
    if not callable(poll):
        return None
    try:
        return poll()
    except OSError:
        return None


def _snapshot_process_tree(root_pid: int | None) -> dict[str, Any]:
    if root_pid is None:
        return {"available": False, "root_exists": False, "children": []}
    try:
        import psutil  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return {"available": False, "root_exists": False, "children": []}

    try:
        root_process = psutil.Process(root_pid)
        try:
            children = [
                _snapshot_child_process(child, psutil)
                for child in root_process.children(recursive=True)
            ]
        finally:
            del root_process
        return {
            "available": True,
            "root_exists": True,
            "children": children,
        }
    except psutil.NoSuchProcess:
        return {"available": True, "root_exists": False, "children": []}
    except psutil.AccessDenied:
        return {
            "available": True,
            "root_exists": True,
            "children": [],
            "error": "AccessDenied",
        }
    except OSError as exc:
        return {
            "available": True,
            "root_exists": False,
            "children": [],
            "error": type(exc).__name__,
        }


def _snapshot_child_process(process: Any, psutil_module: Any) -> dict[str, Any]:
    try:
        with process.oneshot():
            pid = int(process.pid)
            ppid = int(process.ppid())
            name = str(process.name())
            status = _normalize_process_status(str(process.status()))
            created_at = _timestamp_to_utc(process.create_time())
    except psutil_module.NoSuchProcess:
        return {
            "pid": int(getattr(process, "pid", 0)),
            "ppid": None,
            "name": "",
            "status": "exited",
            "created_at_utc": "",
        }
    except (psutil_module.AccessDenied, OSError):
        return {
            "pid": int(getattr(process, "pid", 0)),
            "ppid": None,
            "name": "",
            "status": "unknown",
            "created_at_utc": "",
        }
    finally:
        del process

    return {
        "pid": pid,
        "ppid": ppid,
        "name": name,
        "status": status,
        "created_at_utc": created_at,
    }


def _normalize_process_status(raw_status: str) -> str:
    status = raw_status.casefold()
    if status in {"zombie", "dead"}:
        return "exited"
    if not status:
        return "unknown"
    return "running"


def _append_once_for_observation_state(
    workspace: Path,
    previous_state: str,
    current_state: str,
    event_type: str,
    case_id: str,
    sample_id: str,
    message: str,
    data: Mapping[str, Any],
) -> None:
    if previous_state == current_state:
        return
    append_event(
        workspace,
        event_type=event_type,
        case_id=case_id,
        sample_id=sample_id,
        message=message,
        data=data,
    )
    if event_type != "execution_recorded":
        append_event(
            workspace,
            event_type="execution_recorded",
            case_id=case_id,
            sample_id=sample_id,
            message="execution observation state recorded",
            data={"execution_state": current_state, **dict(data)},
        )


def _elapsed_seconds(started_at: object, observed_at: str) -> float:
    started = _parse_utc_timestamp(str(started_at or ""))
    observed = _parse_utc_timestamp(observed_at)
    if started is None or observed is None:
        return 0.0
    return max(0.0, (observed - started).total_seconds())


def _parse_utc_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        decoded = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if decoded.tzinfo is None:
        return decoded.replace(tzinfo=timezone.utc)
    return decoded.astimezone(timezone.utc)


def _timestamp_to_utc(value: float) -> str:
    return (
        datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
    )


def _no_window_creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))


def _case_sample_id(workspace: Path) -> str:
    state = _read_json_file(workspace / "case_state.json")
    sample_metadata = _read_json_file(workspace / "sample" / "sample.json")
    return str(sample_metadata.get("sample_id") or state.get("sample_id") or "")


def _find_forbidden_action_fields(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in FORBIDDEN_ACTION_FIELDS:
                found.add(str(key))
            found.update(_find_forbidden_action_fields(child))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_forbidden_action_fields(item))
    return found


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
