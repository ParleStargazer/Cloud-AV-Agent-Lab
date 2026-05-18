from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import WorkspaceError, WorkspaceNotFoundError
from .io import (
    _case_sample_id,
    _coerce_int,
    _read_json_file,
    _read_recent_events,
    _utc_now,
    append_event,
    write_case_state,
)
from .paths import (
    _case_workspace,
    _is_relative_to,
    safe_case_id,
    safe_original_filename,
)
from .reports import write_case_report

TERMINAL_EXECUTION_STATES = {
    "exited_cleanly",
    "exited_with_error",
    "launch_failed",
    "terminated_or_disappeared",
}
FORBIDDEN_WORKER_ACTION_FIELDS = {
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
ALLOWED_WORKER_ACTION_FIELDS = {
    "action",
    "expected_sha256",
    "run_id",
    "sample_id",
}


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


def prepare_worker_execute_request(
    workdir: str | Path,
    case_id: str,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    forbidden_fields = sorted(_find_forbidden_worker_action_fields(payload))
    if forbidden_fields:
        raise WorkspaceError(
            "case action payload contains forbidden execution fields: "
            + ", ".join(forbidden_fields)
        )
    unsupported_fields = sorted(
        str(key) for key in set(payload) - ALLOWED_WORKER_ACTION_FIELDS
    )
    if unsupported_fields:
        raise WorkspaceError(
            "case action payload contains unsupported execution fields: "
            + ", ".join(unsupported_fields)
        )
    safe_id = safe_case_id(case_id)
    workspace = _case_workspace(workdir, safe_id)
    if not workspace.is_dir():
        raise WorkspaceNotFoundError(
            "case workspace does not exist; run guest-prepare-case first"
        )
    context = _uploaded_sample_execution_context(workspace, payload)
    sample_path = context["sample_path"]
    if not os.path.exists(sample_path):
        append_event(
            workspace,
            event_type="sample_missing_before_execution",
            case_id=safe_id,
            sample_id=context["sample_id"],
            message="uploaded sample was missing before execution",
            data={"sample_path_under_case": True, "execution_via": "desktop_worker"},
        )
        write_case_report(workspace)
        raise WorkspaceError("uploaded sample is missing before execution")

    expected_sha256 = str(payload.get("expected_sha256", "")).strip()
    if not expected_sha256:
        expected_sha256 = str(context["sample_metadata"].get("sha256", "")).strip()
    run_id = str(payload.get("run_id") or safe_id).strip()
    append_event(
        workspace,
        event_type="execution_requested",
        case_id=safe_id,
        sample_id=context["sample_id"],
        message="controlled execution requested for Desktop Worker",
        data={
            "run_id": run_id,
            "sample_path_under_case": True,
            "expected_sha256_match": context["expected_sha256_match"],
            "execution_via": "desktop_worker",
        },
    )
    return {
        "case_id": safe_id,
        "sample_id": str(context["sample_id"]),
        "run_id": run_id,
        "expected_sha256": expected_sha256,
    }


def _find_forbidden_worker_action_fields(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in FORBIDDEN_WORKER_ACTION_FIELDS:
                found.add(str(key))
            found.update(_find_forbidden_worker_action_fields(child))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_forbidden_worker_action_fields(item))
    return found


def record_worker_execution_started(
    workdir: str | Path,
    case_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return _record_worker_execution(
        workdir=workdir,
        case_id=case_id,
        payload=payload,
        event_type="execution_started",
        phase="execution_started",
        message="uploaded sample process started by Desktop Worker",
    )


def record_worker_execution_observed(
    workdir: str | Path,
    case_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return _record_worker_execution(
        workdir=workdir,
        case_id=case_id,
        payload=payload,
        event_type="execution_observed",
        phase="execution_observed",
        message="Desktop Worker execution status observed",
    )


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


def _record_worker_execution(
    *,
    workdir: str | Path,
    case_id: str,
    payload: Mapping[str, Any],
    event_type: str,
    phase: str,
    message: str,
) -> dict[str, Any]:
    safe_id = safe_case_id(case_id)
    workspace = _case_workspace(workdir, safe_id)
    if not workspace.is_dir():
        raise WorkspaceNotFoundError(
            "case workspace does not exist; run guest-prepare-case first"
        )
    state = _read_json_file(workspace / "case_state.json")
    worker_execution = payload.get("worker_execution")
    worker_execution = (
        dict(worker_execution) if isinstance(worker_execution, Mapping) else {}
    )
    execution_state = str(
        payload.get("execution_state")
        or worker_execution.get("state")
        or worker_execution.get("status")
        or "unknown"
    )
    observed_at = str(
        payload.get("observed_at_utc")
        or worker_execution.get("last_observed_at_utc")
        or _utc_now()
    )
    sample_id = str(
        payload.get("sample_id")
        or worker_execution.get("sample_id")
        or _case_sample_id(workspace)
    )
    previous_execution = state.get("execution")
    execution = (
        dict(previous_execution) if isinstance(previous_execution, Mapping) else {}
    )
    execution.update(worker_execution)
    children = (
        list(payload.get("children", []))
        if isinstance(payload.get("children"), list)
        else list(worker_execution.get("children", []))
        if isinstance(worker_execution.get("children"), list)
        else []
    )
    execution.update(
        {
            "enabled": True,
            "requested": True,
            "state": execution_state,
            "root_pid": _coerce_int(
                payload.get("root_pid") or worker_execution.get("root_pid")
            ),
            "pid": _coerce_int(payload.get("pid") or worker_execution.get("pid")),
            "sample_id": sample_id,
            "expected_sha256": str(
                payload.get("expected_sha256")
                or worker_execution.get("expected_sha256")
                or execution.get("expected_sha256", "")
            ),
            "run_id": str(
                payload.get("run_id")
                or worker_execution.get("run_id")
                or execution.get("run_id", "")
            ),
            "sample_path_under_case": bool(
                payload.get("sample_path_under_case")
                or worker_execution.get("sample_path_under_case")
            ),
            "execution_via": "desktop_worker",
            "last_observed_at_utc": observed_at,
            "exit_code": _coerce_int(
                payload.get("exit_code") or worker_execution.get("exit_code")
            ),
            "children": children,
        }
    )
    if payload.get("started_at_utc") or worker_execution.get("started_at_utc"):
        execution["started_at_utc"] = str(
            payload.get("started_at_utc") or worker_execution.get("started_at_utc")
        )
    updated_state = dict(state)
    updated_state["phase"] = phase
    updated_state["execution"] = execution
    updated_state["updated_at_utc"] = observed_at
    write_case_state(workspace, updated_state)

    event_data = {
        "root_pid": execution.get("root_pid"),
        "run_id": execution.get("run_id", ""),
        "execution_state": execution_state,
        "exit_code": execution.get("exit_code"),
        "children_count": len(children),
        "execution_via": "desktop_worker",
        "worker_pid": payload.get("worker_pid") or worker_execution.get("worker_pid"),
        "worker_session_id": payload.get("worker_session_id")
        or worker_execution.get("worker_session_id"),
        "low_intrusion_observation": True,
    }
    append_event(
        workspace,
        event_type=event_type,
        case_id=safe_id,
        sample_id=sample_id,
        message=message,
        data=event_data,
    )
    if event_type == "execution_observed":
        if children:
            append_event(
                workspace,
                event_type="execution_child_observed",
                case_id=safe_id,
                sample_id=sample_id,
                message="child processes observed by Desktop Worker",
                data={
                    "root_pid": execution.get("root_pid"),
                    "children_count": len(children),
                    "execution_via": "desktop_worker",
                },
            )
        if execution_state in {"exited_cleanly", "exited_with_error"}:
            append_event(
                workspace,
                event_type="execution_exited",
                case_id=safe_id,
                sample_id=sample_id,
                message="root process exited; no AV verdict is inferred",
                data={
                    "root_pid": execution.get("root_pid"),
                    "exit_code": execution.get("exit_code"),
                    "execution_via": "desktop_worker",
                },
            )
        elif execution_state == "timeout_still_running":
            append_event(
                workspace,
                event_type="execution_timeout_still_running",
                case_id=safe_id,
                sample_id=sample_id,
                message="polling window ended while process tree was still running",
                data={
                    "root_pid": execution.get("root_pid"),
                    "children_count": len(children),
                    "execution_via": "desktop_worker",
                },
            )
        elif execution_state == "terminated_or_disappeared":
            append_event(
                workspace,
                event_type="execution_recorded",
                case_id=safe_id,
                sample_id=sample_id,
                message=(
                    "process is no longer observable; no AV verdict is inferred "
                    "from this observation alone"
                ),
                data={
                    "root_pid": execution.get("root_pid"),
                    "execution_via": "desktop_worker",
                },
            )
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
