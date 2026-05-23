from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import WorkspaceError, WorkspaceNotFoundError
from .io import (
    _base_case_state,
    _coerce_int,
    _read_json_file,
    _read_recent_events,
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

LOGGER = logging.getLogger("cloud_av_agent_lab.guest_agent_server.workspace")


@dataclass(frozen=True)
class FileProbe:
    exists: bool
    stat_ok: bool | None
    size: int | None = None
    error: str = ""
    probe_kind: str = "presence"


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
        product_id=str(state.get("product_id", "")),
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
