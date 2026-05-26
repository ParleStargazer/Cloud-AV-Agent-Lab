from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cloud_av_agent_lab.core.execution_modes import resolve_execution_mode
from cloud_av_agent_lab.core.os_error_details import (
    format_os_error_details,
    safe_os_error_details,
)
from cloud_av_agent_lab.desktop_worker.lease import (
    ExecutionLeaseError,
    verify_execution_lease,
)
from cloud_av_agent_lab.desktop_worker.status import current_process_session_id
from cloud_av_agent_lab.guest_agent_server.workspace.io import (
    _coerce_int,
    _read_json_file,
    _utc_now,
)
from cloud_av_agent_lab.guest_agent_server.workspace.paths import (
    _case_workspace,
    _is_relative_to,
    safe_case_id,
    safe_original_filename,
)

ALLOWED_EXECUTE_FIELDS = {
    "case_id",
    "handler_id",
    "sample_id",
    "run_id",
    "expected_sha256",
    "execution_lease",
}
FORBIDDEN_EXECUTE_FIELDS = {
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
TERMINAL_EXECUTION_STATES = {
    "exited_cleanly",
    "exited_with_error",
    "launch_failed",
    "terminated_or_disappeared",
}
CHILD_ENV_ALLOWLIST = {
    "APPDATA",
    "LOCALAPPDATA",
    "PATH",
    "PROGRAMDATA",
    "SystemRoot",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}
FIXED_CMD_EXE = Path(r"C:\Windows\System32\cmd.exe")
CHILD_ENV_DENYLIST = {
    "ALL_PROXY",
    "CLOUD_AV_DESKTOP_WORKER_TOKEN",
    "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN",
    "CLOUD_AV_GUEST_AGENT_TOKEN",
    "CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN",
    "EXECUTION_TOKEN",
    "GUEST_AGENT_TOKEN",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "REAL_CONFIG_PATH",
    "TENCENTCLOUD_SECRET_ID",
    "TENCENTCLOUD_SECRET_KEY",
}


class WorkerExecutionError(RuntimeError):
    """Raised when Desktop Worker cannot safely execute or observe a case."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class WorkerExecutionRegistry:
    """State owned by one Desktop Worker process.

    The lock protects lease nonce consumption and the single active execution
    window. Process objects are kept only for Popen.poll(); psutil objects are
    never cached.
    """

    workdir: Path
    lease_secret: str
    consumed_nonces: set[str] = field(default_factory=set)
    processes: dict[str, Any] = field(default_factory=dict)
    active_case_id: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def busy(self) -> bool:
        with self.lock:
            return bool(self.active_case_id)

    def execute(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = _validate_execute_payload(payload)
        context = _execution_context(self.workdir, request)
        lease_payload = _verify_request_lease(
            request=request,
            lease_secret=self.lease_secret,
        )

        expected_sha256 = request["expected_sha256"]
        actual_sha256 = _sha256_file(context["sample_path"])
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise WorkerExecutionError(
                "expected_sha256 does not match uploaded sample bytes"
            )

        with self.lock:
            if self.active_case_id:
                raise WorkerExecutionError("desktop worker is busy", status_code=409)
            if _execution_already_recorded(context["workspace"]):
                raise WorkerExecutionError(
                    "case already has an execution attempt recorded",
                    status_code=409,
                )
            nonce = lease_payload["nonce"]
            if nonce in self.consumed_nonces:
                raise WorkerExecutionError(
                    "execution lease has already been consumed",
                    status_code=409,
                )
            self.consumed_nonces.add(nonce)
            self.active_case_id = request["case_id"]

        try:
            return self._launch(context, request, actual_sha256)
        except BaseException:
            with self.lock:
                self.active_case_id = ""
            raise

    def execution_status(
        self,
        case_id: str,
        *,
        mark_timeout: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        safe_id = safe_case_id(case_id)
        workspace = _case_workspace(self.workdir, safe_id)
        if not workspace.is_dir():
            raise WorkerExecutionError(
                "case workspace does not exist; run guest-prepare-case first",
                status_code=404,
            )

        state_path = _worker_state_path(workspace)
        if not state_path.exists():
            return {
                "case_id": safe_id,
                "sample_id": _case_sample_id(workspace),
                "run_id": "",
                "execution_state": "not_started",
                "root_pid": None,
                "exit_code": None,
                "children": [],
                "observed_at_utc": _utc_now(),
                "worker_execution": {"state": "not_started"},
            }

        state = _read_json_file(state_path)
        observed = self._observe(workspace, state, mark_timeout, timeout_seconds)
        return {
            "case_id": safe_id,
            "sample_id": str(observed.get("sample_id", "")),
            "run_id": str(observed.get("run_id", "")),
            "execution_state": str(observed.get("state", "unknown")),
            "root_pid": observed.get("root_pid"),
            "exit_code": observed.get("exit_code"),
            "children": list(observed.get("children", []))
            if isinstance(observed.get("children"), list)
            else [],
            "observed_at_utc": str(observed.get("last_observed_at_utc", "")),
            "worker_execution": observed,
        }

    def _launch(
        self,
        context: Mapping[str, Any],
        request: Mapping[str, str],
        actual_sha256: str,
    ) -> dict[str, Any]:
        workspace = context["workspace"]
        sample_dir = context["sample_dir"]
        decision = context["execution_mode"]
        command = _execution_command(context)
        started_at = _utc_now()
        try:
            process = subprocess.Popen(  # noqa: S603
                command,
                cwd=str(sample_dir),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_no_window_creationflags(),
                close_fds=True,
                env=_minimal_child_env(os.environ),
            )
        except OSError as exc:
            failed_at = _utc_now()
            error_details = safe_os_error_details(exc)
            state = _base_worker_execution_state(
                request=request,
                context=context,
                actual_sha256=actual_sha256,
                execution_state="launch_failed",
                root_pid=None,
                started_at="",
                observed_at=failed_at,
            )
            state["error"] = error_details["type"]
            state["error_details"] = error_details
            _write_worker_state(workspace, state)
            raise WorkerExecutionError(
                "uploaded sample failed to start: "
                + format_os_error_details(error_details)
            ) from exc

        with self.lock:
            self.processes[request["case_id"]] = process

        state = _base_worker_execution_state(
            request=request,
            context=context,
            actual_sha256=actual_sha256,
            execution_state="running",
            root_pid=process.pid,
            started_at=started_at,
            observed_at=started_at,
        )
        _write_worker_state(workspace, state)
        return {
            "action": "execute_uploaded_sample",
            "execution_state": "running",
            "message": "uploaded sample process started by Desktop Worker",
            "root_pid": process.pid,
            "pid": process.pid,
            "sample_id": request["sample_id"],
            "run_id": request["run_id"],
            "expected_sha256": request["expected_sha256"],
            "started_at_utc": started_at,
            "sample_path_under_case": True,
            "execution_via": "desktop_worker",
            "handler_id": decision.handler_id,
            "execution_mode": decision.execution_mode,
            "interpreter": _interpreter_for(decision.handler_id),
            "client_supplied_command": False,
            "client_supplied_args": False,
            "client_supplied_path": False,
            "hash_verified": actual_sha256 == request["expected_sha256"],
            "worker_pid": os.getpid(),
            "worker_session_id": current_process_session_id(),
        }

    def _observe(
        self,
        workspace: Path,
        state: Mapping[str, Any],
        mark_timeout: bool,
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        case_id = str(state.get("case_id", ""))
        root_pid = _coerce_int(state.get("root_pid") or state.get("pid"))
        previous_state = str(state.get("state", "unknown"))
        process = self.processes.get(case_id)
        registry_exit_code = _poll_registered_process(process)
        recorded_exit_code = _coerce_int(state.get("exit_code"))
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
            execution_state = (
                "exited_cleanly" if exit_code == 0 else "exited_with_error"
            )
        elif registered_running or root_observable or children_running:
            execution_state = "running"
            exit_code = None
        elif psutil_available:
            execution_state = "terminated_or_disappeared"
        else:
            execution_state = "unknown"

        observed_at = _utc_now()
        if (
            mark_timeout
            and execution_state == "running"
            and timeout_seconds is not None
            and _elapsed_seconds(state.get("started_at_utc"), observed_at)
            >= timeout_seconds
        ):
            execution_state = "timeout_still_running"

        observation_count = (_coerce_int(state.get("observation_count")) or 0) + 1
        updated = dict(state)
        updated.update(
            {
                "state": execution_state,
                "status": execution_state,
                "root_pid": root_pid,
                "pid": root_pid,
                "exit_code": exit_code,
                "children": children,
                "last_observed_at_utc": observed_at,
                "observation_count": observation_count,
                "low_intrusion_observation": True,
            }
        )
        _write_worker_state(workspace, updated)
        if execution_state in TERMINAL_EXECUTION_STATES or execution_state in {
            "timeout_still_running",
            "exited_cleanly",
            "exited_with_error",
        }:
            with self.lock:
                self.processes.pop(case_id, None)
                if self.active_case_id == case_id:
                    self.active_case_id = ""
        return updated


def _validate_execute_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise WorkerExecutionError("execute payload must be a JSON object")
    unknown = set(payload) - ALLOWED_EXECUTE_FIELDS
    forbidden = {
        key
        for key in payload
        if str(key).casefold().replace("-", "_") in FORBIDDEN_EXECUTE_FIELDS
    }
    if unknown or forbidden:
        fields = sorted(str(field) for field in (unknown | forbidden))
        raise WorkerExecutionError(
            "execute payload contains forbidden or unsupported fields: "
            + ", ".join(fields)
        )

    case_id = safe_case_id(payload.get("case_id"))
    request = {
        "case_id": case_id,
        "sample_id": str(payload.get("sample_id", "")).strip(),
        "run_id": str(payload.get("run_id", "")).strip(),
        "expected_sha256": str(payload.get("expected_sha256", "")).strip(),
        "execution_lease": str(payload.get("execution_lease", "")).strip(),
        "handler_id": str(payload.get("handler_id", "")).strip(),
    }
    missing = [
        key for key, value in request.items() if not value and key != "handler_id"
    ]
    if missing:
        raise WorkerExecutionError(
            "execute payload is missing required fields: " + ", ".join(missing)
        )
    return request


def _execution_context(
    workdir: Path,
    request: Mapping[str, str],
) -> dict[str, Any]:
    workspace = _case_workspace(workdir, request["case_id"])
    if not workspace.is_dir():
        raise WorkerExecutionError(
            "case workspace does not exist; run guest-prepare-case first",
            status_code=404,
        )
    state = _read_json_file(workspace / "case_state.json")
    sample_metadata = _read_json_file(workspace / "sample" / "sample.json")
    if sample_metadata.get("saved_once") is not True:
        raise WorkerExecutionError(
            "execute_uploaded_sample requires a previously uploaded sample"
        )
    sample_id = str(sample_metadata.get("sample_id") or state.get("sample_id") or "")
    if request["sample_id"] != sample_id:
        raise WorkerExecutionError(
            "sample_id does not match the prepared case metadata"
        )
    recorded_sha256 = str(sample_metadata.get("sha256", "")).strip()
    if recorded_sha256 and request["expected_sha256"] != recorded_sha256:
        raise WorkerExecutionError(
            "expected_sha256 does not match uploaded sample metadata"
        )

    stored_filename = safe_original_filename(
        sample_metadata.get("stored_filename")
        or sample_metadata.get("original_filename")
    )
    decision = resolve_execution_mode(stored_filename)
    requested_handler = str(request.get("handler_id", "")).strip()
    if requested_handler and requested_handler != decision.handler_id:
        raise WorkerExecutionError(
            "handler_id does not match the registered uploaded sample type"
        )
    if not decision.enabled:
        reason = decision.reason_code or "execution_handler_disabled"
        raise WorkerExecutionError(
            f"execution handler is disabled or unsupported: {reason}"
        )
    sample_dir = (workspace / "sample").resolve()
    sample_path = (sample_dir / stored_filename).resolve()
    if not _is_relative_to(sample_path, sample_dir):
        raise WorkerExecutionError("sample path escapes the sample directory")
    if not os.path.exists(sample_path):
        raise WorkerExecutionError("uploaded sample is missing before execution")
    return {
        "workspace": workspace,
        "state": state,
        "sample_metadata": sample_metadata,
        "sample_id": sample_id,
        "sample_dir": sample_dir,
        "sample_path": sample_path,
        "stored_filename": stored_filename,
        "execution_mode": decision,
    }


def _verify_request_lease(
    *,
    request: Mapping[str, str],
    lease_secret: str,
) -> dict[str, str]:
    try:
        return verify_execution_lease(
            token=request["execution_lease"],
            secret=lease_secret,
            expected={
                "case_id": request["case_id"],
                "sample_id": request["sample_id"],
                "run_id": request["run_id"],
                "expected_sha256": request["expected_sha256"],
            },
        )
    except ExecutionLeaseError as exc:
        raise WorkerExecutionError(str(exc), status_code=403) from exc


def _execution_already_recorded(workspace: Path) -> bool:
    state_path = _worker_state_path(workspace)
    if not state_path.exists():
        return False
    state = _read_json_file(state_path)
    return (_coerce_int(state.get("execution_attempt")) or 0) > 0


def _base_worker_execution_state(
    *,
    request: Mapping[str, str],
    context: Mapping[str, Any],
    actual_sha256: str,
    execution_state: str,
    root_pid: int | None,
    started_at: str,
    observed_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": "desktop-worker-execution.v1",
        "case_id": request["case_id"],
        "sample_id": request["sample_id"],
        "run_id": request["run_id"],
        "execution_attempt": 1,
        "single_execution_only": True,
        "state": execution_state,
        "status": execution_state,
        "root_pid": root_pid,
        "pid": root_pid,
        "started_at_utc": started_at,
        "last_observed_at_utc": observed_at,
        "worker_pid": os.getpid(),
        "worker_session_id": current_process_session_id(),
        "exit_code": None,
        "children": [],
        "observation_count": 0,
        "sample_id_match": True,
        "expected_sha256": request["expected_sha256"],
        "actual_sha256": actual_sha256,
        "expected_sha256_match": actual_sha256 == request["expected_sha256"],
        "stored_filename": str(context["stored_filename"]),
        "handler_id": context["execution_mode"].handler_id,
        "execution_mode": context["execution_mode"].execution_mode,
        "interpreter": _interpreter_for(context["execution_mode"].handler_id),
        "client_supplied_command": False,
        "client_supplied_args": False,
        "client_supplied_path": False,
        "hash_verified": actual_sha256 == request["expected_sha256"],
        "sample_path_under_case": True,
        "execution_via": "desktop_worker",
        "low_intrusion_observation": True,
    }


def _execution_command(context: Mapping[str, Any]) -> list[str]:
    sample_path = context["sample_path"]
    decision = context["execution_mode"]
    if decision.handler_id == "batch_script":
        return [str(FIXED_CMD_EXE), "/d", "/c", "call", str(sample_path)]
    return [str(sample_path)]


def _interpreter_for(handler_id: str) -> str:
    if handler_id == "batch_script":
        return str(FIXED_CMD_EXE)
    return ""


def _write_worker_state(workspace: Path, state: Mapping[str, Any]) -> None:
    import json

    state_dir = workspace / "worker-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    _worker_state_path(workspace).write_text(
        json.dumps(dict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _worker_state_path(workspace: Path) -> Path:
    return workspace / "worker-state" / "worker_execution_state.json"


def _case_sample_id(workspace: Path) -> str:
    state = _read_json_file(workspace / "case_state.json")
    sample_metadata = _read_json_file(workspace / "sample" / "sample.json")
    return str(sample_metadata.get("sample_id") or state.get("sample_id") or "")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _minimal_child_env(source: Mapping[str, str]) -> dict[str, str]:
    allowed_lookup = {key.casefold(): key for key in CHILD_ENV_ALLOWLIST}
    denied = {key.casefold() for key in CHILD_ENV_DENYLIST}
    child_env: dict[str, str] = {}
    for key, value in source.items():
        folded = key.casefold()
        if folded in denied:
            continue
        if folded.startswith("cloud_av_") and folded.endswith("_secret"):
            continue
        allowed_key = allowed_lookup.get(folded)
        if allowed_key is None:
            continue
        child_env[allowed_key] = value
    return child_env


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
        return {"available": True, "root_exists": True, "children": children}
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
