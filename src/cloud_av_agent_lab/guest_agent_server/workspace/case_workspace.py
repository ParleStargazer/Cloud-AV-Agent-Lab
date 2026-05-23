from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import WorkspaceError, WorkspaceNotFoundError
from .io import append_event, write_case_state, _base_case_state, _utc_now
from .paths import (
    _case_workspace,
    _is_relative_to,
    safe_case_id,
    safe_original_filename,
)
from .reports import write_case_report

LOGGER = logging.getLogger("cloud_av_agent_lab.guest_agent_server.workspace")


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
    product_id = _payload_product_id(payload)
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
        product_id=product_id,
    )
    write_case_state(workspace, state)
    append_event(
        workspace,
        event_type="case_prepared",
        case_id=case_id,
        sample_id=sample_id,
        message="case workspace prepared",
        data={"workspace": str(workspace), "product_id": product_id},
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

    existing_state = _read_existing_case_state(workspace)
    product_id = str(existing_state.get("product_id", "")) or _workspace_product_id(
        workspace
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
        product_id=product_id,
    )
    write_case_state(workspace, state)
    write_case_report(workspace)
    return sample_path, metadata


def _payload_sample_id(payload: Mapping[str, Any]) -> str:
    sample_value = payload.get("sample")
    if isinstance(sample_value, Mapping):
        return str(sample_value.get("id", ""))
    return ""


def _payload_product_id(payload: Mapping[str, Any]) -> str:
    product_value = payload.get("product")
    if isinstance(product_value, Mapping):
        return str(product_value.get("id", "")).strip()
    vm_value = payload.get("vm")
    if isinstance(vm_value, Mapping):
        return str(vm_value.get("product_id", "")).strip()
    return ""


def _read_existing_case_state(workspace: Path) -> dict[str, Any]:
    state_path = workspace / "case_state.json"
    if not state_path.exists():
        return {}
    try:
        decoded = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _workspace_product_id(workspace: Path) -> str:
    case_path = workspace / "case.json"
    if not case_path.exists():
        return ""
    try:
        decoded = json.loads(case_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    if not isinstance(decoded, Mapping):
        return ""
    return _payload_product_id(decoded)
