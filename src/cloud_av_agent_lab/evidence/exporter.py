from __future__ import annotations

import json
import os
import zipfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import build_manifest, sha256_bytes

EVIDENCE_BUNDLE_PREFIX = "case_evidence_"
ALLOWED_DIRECT_FILES = (
    "case.json",
    "case_state.json",
    "case_report.json",
    "case_collection.json",
    "case_summary.json",
    "case_summary.md",
    "events.jsonl",
)
ALLOWED_DIRECTORIES = ("collection", "worker-state")
VIRTUAL_NORMALIZED_EVIDENCE = "collector/normalized_evidence.json"
SENSITIVE_NAME_MARKERS = (
    ".env",
    "token",
    "credential",
    "secret",
    "secrets",
    "cloudkey",
    "cloud_key",
    ".worker_secret",
)
SENSITIVE_SUFFIXES = (".local.toml", ".secret.toml", ".secrets.toml")


def build_evidence_bundle(
    workspace: Path,
    output_path: Path,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    entries, excluded = _workspace_entries(workspace, output_path)
    _add_normalized_evidence(entries, workspace)
    generated_at = _utc_now()
    files = [
        {
            "path": name,
            "size": len(content),
            "sha256": sha256_bytes(content),
            "category": _category_for_entry(name),
        }
        for name, content in sorted(entries.items())
    ]
    manifest = build_manifest(
        case_id=_case_id_from_entries(entries),
        product_id=_product_id_from_entries(entries),
        generated_at_utc=generated_at,
        files=files,
        included_paths=sorted(entries),
        excluded_paths=[item["path"] for item in excluded],
        excluded_path_details=excluded,
    )
    entries["manifest.json"] = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in sorted(entries.items()):
            bundle.writestr(name, content)

    bundle_bytes = output_path.read_bytes()
    return {
        "bundle_path": str(output_path),
        "filename": output_path.name,
        "size": len(bundle_bytes),
        "sha256": sha256_bytes(bundle_bytes),
        "manifest": manifest,
    }


def _workspace_entries(
    workspace: Path,
    output_path: Path,
) -> tuple[dict[str, bytes], list[dict[str, str]]]:
    entries: dict[str, bytes] = {}
    excluded: list[dict[str, str]] = []
    workspace_root = workspace.resolve()
    output_resolved = output_path.resolve()
    for source in _iter_workspace_files(workspace):
        if source.is_symlink() or _is_junction(source):
            excluded.append(
                {
                    "path": _relative_name_unresolved(workspace, source) or str(source),
                    "reason": "symlink_or_junction_not_followed",
                }
            )
            continue
        rel = _relative_name(workspace_root, source)
        if not rel:
            excluded.append({"path": str(source), "reason": "path_outside_workspace"})
            continue
        if source.resolve() == output_resolved:
            excluded.append({"path": rel, "reason": "evidence_output_file"})
            continue
        reason = _exclude_reason(rel)
        if reason:
            excluded.append({"path": rel, "reason": reason})
            continue
        if not _is_allowed_artifact(rel):
            excluded.append({"path": rel, "reason": "not_in_allowed_roots"})
            continue
        try:
            entries[rel] = source.read_bytes()
        except OSError as exc:
            excluded.append(
                {"path": rel, "reason": f"read_failed:{type(exc).__name__}"}
            )
    return entries, excluded


def _add_normalized_evidence(entries: dict[str, bytes], workspace: Path) -> None:
    collection = _read_json(workspace / "case_collection.json")
    events = collection.get("events")
    if not isinstance(events, list):
        events = []
    payload = {
        "case_id": collection.get("case_id", ""),
        "sample_id": collection.get("sample_id", ""),
        "product_id": collection.get("product_id", ""),
        "evidence_count": collection.get("evidence_count", 0),
        "events": events,
    }
    entries[VIRTUAL_NORMALIZED_EVIDENCE] = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def _iter_workspace_files(workspace: Path) -> list[Path]:
    files: list[Path] = []
    stack = [workspace]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if entry.is_symlink() or _is_junction(path):
                        files.append(path)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(path)
        except OSError:
            continue
    return files


def _is_allowed_artifact(rel: str) -> bool:
    if rel in ALLOWED_DIRECT_FILES:
        return True
    if rel == "sample/sample.json":
        return True
    return any(rel.startswith(directory + "/") for directory in ALLOWED_DIRECTORIES)


def _exclude_reason(rel: str) -> str:
    lowered = rel.casefold().replace("\\", "/")
    name = Path(rel).name.casefold()
    if not _is_safe_zip_name(rel):
        return "unsafe_zip_path"
    if lowered == "sample/sample.json":
        return ""
    if lowered.startswith("sample/"):
        return "uploaded_sample_bytes"
    if lowered.startswith("evidence/") and lowered.endswith(".zip"):
        return "recursive_evidence_zip"
    if lowered.startswith("configs/"):
        return "config_file"
    if lowered == "configs/real.toml":
        return "real_cloud_config"
    if name.endswith(SENSITIVE_SUFFIXES):
        return "sensitive_config_name"
    if any(marker in name for marker in SENSITIVE_NAME_MARKERS):
        return "suspected_secret_name"
    return ""


def _relative_name(workspace_root: Path, source: Path) -> str:
    try:
        relative = source.resolve().relative_to(workspace_root)
    except ValueError:
        return ""
    return relative.as_posix()


def _relative_name_unresolved(workspace: Path, source: Path) -> str:
    try:
        return source.relative_to(workspace).as_posix()
    except ValueError:
        return ""


def _is_safe_zip_name(name: str) -> bool:
    if not name or name.startswith(("/", "\\")):
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("../") or "/../" in normalized or normalized == "..":
        return False
    if len(normalized) >= 2 and normalized[1] == ":":
        return False
    return not normalized.startswith("//")


def _is_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            return bool(is_junction())
        except OSError:
            return True
    return False


def _category_for_entry(name: str) -> str:
    if name.startswith("collection/"):
        return "collector_artifact"
    if name.startswith("worker-state/"):
        return "worker_state"
    if name == VIRTUAL_NORMALIZED_EVIDENCE:
        return "normalized_evidence"
    if name == "sample/sample.json":
        return "sample_metadata"
    if name.endswith(".json") or name.endswith(".jsonl") or name.endswith(".md"):
        return "case_metadata"
    return "workspace_artifact"


def _case_id_from_entries(entries: Mapping[str, bytes]) -> str:
    for filename in ("case_summary.json", "case_report.json", "case_state.json"):
        payload = _decode_entry(entries.get(filename, b""))
        case_id = payload.get("case_id")
        if case_id:
            return str(case_id)
    return ""


def _product_id_from_entries(entries: Mapping[str, bytes]) -> str:
    summary = _decode_entry(entries.get("case_summary.json", b""))
    if summary.get("product_id"):
        return str(summary["product_id"])
    report = _decode_entry(entries.get("case_report.json", b""))
    if report.get("product_id"):
        return str(report["product_id"])
    collection = _decode_entry(entries.get("case_collection.json", b""))
    return str(collection.get("product_id", ""))


def _decode_entry(content: bytes) -> dict[str, Any]:
    if not content:
        return {}
    try:
        decoded = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
