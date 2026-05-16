from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cloud_av_agent_lab.evaluation import evaluate_case, render_summary_markdown
from cloud_av_agent_lab.evidence import build_evidence_bundle

from .errors import WorkspaceNotFoundError
from .io import _read_json_file, _read_recent_events
from .paths import _case_workspace, safe_case_id
from .reports import write_case_report


def read_case_summary(
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
    return write_case_summary(workspace, max_events=max_events)


def write_case_summary(workspace: Path, max_events: int = 20) -> dict[str, Any]:
    case_report = write_case_report(workspace, max_events=max_events)
    case_collection = _read_json_file(workspace / "case_collection.json")
    events = _read_recent_events(workspace / "events.jsonl", max_events=1000)
    summary = evaluate_case(
        case_report=case_report,
        case_collection=case_collection,
        events=events,
        max_timeline_events=max_events,
    )
    payload = summary.to_dict()
    (workspace / "case_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (workspace / "case_summary.md").write_text(
        render_summary_markdown(summary),
        encoding="utf-8",
    )
    return payload


def export_case_evidence_bundle(
    workdir: str | Path,
    case_id: str,
) -> dict[str, Any]:
    safe_id = safe_case_id(case_id)
    workspace = _case_workspace(workdir, safe_id)
    if not workspace.is_dir():
        raise WorkspaceNotFoundError(
            "case workspace does not exist; run guest-prepare-case first"
        )
    write_case_summary(workspace)
    output_path = workspace / "evidence" / f"case_evidence_{safe_id}.zip"
    return build_evidence_bundle(workspace, output_path)
