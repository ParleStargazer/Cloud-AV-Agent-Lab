from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import WorkspaceError, WorkspaceNotFoundError
from .execution import (
    ExecutionRegistry,
    _execute_uploaded_sample,
    _uploaded_sample_execution_context,
)
from .io import _case_sample_id, append_event
from .paths import _case_workspace, safe_case_id
from .reports import write_case_report
from .sample_status import _probe_sample_current_status, read_case_status

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
