from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
    SecurityProductReadinessContext,
    SecurityProductReadinessResult,
    run_security_product_readiness_probe,
)

from .errors import WorkspaceNotFoundError
from .io import (
    _case_sample_id,
    _read_json_file,
    _read_recent_events,
    _utc_now,
    append_event,
    write_case_state,
)
from .paths import _case_workspace, safe_case_id
from .reports import write_case_report

READINESS_SCHEMA_VERSION = "case-security-product-readiness.v1"


def check_and_record_case_security_product_readiness(
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

    sample_id = _case_sample_id(workspace)
    append_event(
        workspace,
        event_type="security_product_readiness_started",
        case_id=safe_id,
        sample_id=sample_id,
        message="security product readiness check started",
        data={"product_id": safe_product},
    )
    result = run_security_product_readiness_probe(
        SecurityProductReadinessContext(
            product_id=safe_product,
            workspace=workspace,
        ),
        safe_product,
    )
    _append_result_event(workspace, safe_id, sample_id, result)
    payload = write_case_security_product_readiness(
        workspace,
        result,
        max_events=max_events,
    )
    write_case_report(workspace)
    return payload


def read_case_security_product_readiness_status(
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
    readiness_file = workspace / "case_security_product_readiness.json"
    if not readiness_file.is_file():
        state = _read_json_file(workspace / "case_state.json")
        return {
            "schema_version": READINESS_SCHEMA_VERSION,
            "case_id": safe_id,
            "sample_id": str(state.get("sample_id", "")),
            "product_id": "",
            "state": "not_checked",
            "confidence": "low",
            "scope": "log_observability",
            "protection_state": "unknown",
            "checked_at_utc": "",
            "checks": [],
            "warnings": ["security product readiness has not been checked"],
            "errors": [],
            "recent_events": _read_recent_events(
                workspace / "events.jsonl",
                max_events=max_events,
            ),
        }
    payload = _read_json_file(readiness_file)
    payload["recent_events"] = _read_recent_events(
        workspace / "events.jsonl",
        max_events=max_events,
    )
    return payload


def write_case_security_product_readiness(
    workspace: Path,
    result: SecurityProductReadinessResult,
    max_events: int = 20,
) -> dict[str, Any]:
    state = _read_json_file(workspace / "case_state.json")
    sample_id = _case_sample_id(workspace)
    case_id = str(state.get("case_id", ""))
    result_data = result.to_dict()
    payload = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "case_id": case_id,
        "sample_id": sample_id,
        **result_data,
        "recent_events": _read_recent_events(
            workspace / "events.jsonl",
            max_events=max_events,
        ),
    }
    (workspace / "case_security_product_readiness.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _update_case_state_security_product_readiness(workspace, state, result_data)
    return payload


def _update_case_state_security_product_readiness(
    workspace: Path,
    state: Mapping[str, Any],
    result_data: Mapping[str, Any],
) -> None:
    updated_state = dict(state)
    updated_state["security_product_readiness"] = {
        "product_id": str(result_data.get("product_id", "")),
        "state": str(result_data.get("state", "unknown")),
        "confidence": str(result_data.get("confidence", "low")),
        "scope": str(result_data.get("scope", "log_observability")),
        "protection_state": str(result_data.get("protection_state", "unknown")),
        "checked_at_utc": str(result_data.get("checked_at_utc", "")),
        "warnings": list(result_data.get("warnings", []))
        if isinstance(result_data.get("warnings"), list)
        else [],
        "errors": list(result_data.get("errors", []))
        if isinstance(result_data.get("errors"), list)
        else [],
    }
    updated_state["updated_at_utc"] = _utc_now()
    write_case_state(workspace, updated_state)


def _append_result_event(
    workspace: Path,
    case_id: str,
    sample_id: str,
    result: SecurityProductReadinessResult,
) -> None:
    if result.state == "unsupported":
        event_type = "security_product_readiness_unsupported"
        message = "security product readiness probe is unsupported"
    elif result.state in {"unknown", "not_ready"}:
        event_type = "security_product_readiness_failed"
        message = "security product readiness check did not pass"
    else:
        event_type = "security_product_readiness_checked"
        message = "security product readiness checked"
    append_event(
        workspace,
        event_type=event_type,
        case_id=case_id,
        sample_id=sample_id,
        message=message,
        data={
            "product_id": result.product_id,
            "state": result.state,
            "confidence": result.confidence,
            "scope": result.scope,
            "protection_state": result.protection_state,
        },
    )
