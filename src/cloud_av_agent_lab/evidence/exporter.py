from __future__ import annotations

import json
import os
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import (
    MAX_BUNDLE_FILES,
    MAX_BUNDLE_UNCOMPRESSED_BYTES,
    MAX_ENTRY_BYTES,
    build_manifest,
    sha256_bytes,
)
from .redaction import RedactionContext, RedactionError, redact_text_artifact

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
TEXT_SUFFIXES = (".json", ".jsonl", ".md", ".txt")
RAW_BINARY_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".wal", ".shm", ".exe", ".dll")
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
SOURCE_HASH_MAX_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class BundleEntry:
    path: str
    content: bytes
    category: str
    redaction_state: str
    redacted: bool
    encoding: str = "utf-8"
    source_trust: str = "guest_reported"


def build_evidence_bundle(
    workspace: Path,
    output_path: Path,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    collection = _read_json(workspace / "case_collection.json")
    artifact_map, artifact_warnings = _collector_artifact_map(collection)
    redaction_context = _redaction_context(workspace, collection)
    entries, excluded, redacted_files, warnings = _workspace_entries(
        workspace,
        output_path,
        artifact_map,
        redaction_context,
    )
    warnings.extend(artifact_warnings)
    _add_normalized_evidence(
        entries,
        excluded,
        redacted_files,
        warnings,
        workspace,
        artifact_map,
        redaction_context,
    )

    generated_at = _utc_now()
    files = [
        {
            "path": entry.path,
            "size": len(entry.content),
            "sha256": sha256_bytes(entry.content),
            "archive_sha256": sha256_bytes(entry.content),
            "category": entry.category,
            "redaction_state": entry.redaction_state,
            "redacted": entry.redacted,
            "encoding": entry.encoding,
            "source_trust": entry.source_trust,
        }
        for entry in sorted(entries.values(), key=lambda item: item.path)
    ]
    manifest = build_manifest(
        case_id=_case_id_from_entries(entries),
        product_id=_product_id_from_entries(entries),
        generated_at_utc=generated_at,
        files=files,
        included_paths=sorted(entries),
        excluded_paths=[item["path"] for item in excluded],
        excluded_path_details=excluded,
        redacted_files=redacted_files,
        redaction_warnings=warnings,
        raw_binary_included=False,
    )
    zip_entries = {name: entry.content for name, entry in sorted(entries.items())}
    zip_entries["manifest.json"] = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in sorted(zip_entries.items()):
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
    artifact_map: Mapping[str, Mapping[str, Any]],
    context: RedactionContext,
) -> tuple[
    dict[str, BundleEntry],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
]:
    entries: dict[str, BundleEntry] = {}
    excluded: list[dict[str, Any]] = []
    redacted_files: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_bytes = 0
    workspace_root = workspace.resolve()
    output_resolved = output_path.resolve()
    for source in _iter_workspace_files(workspace):
        if source.is_symlink() or _is_junction(source):
            excluded.append(
                _excluded_detail(
                    _relative_name_unresolved(workspace, source) or "<unknown>",
                    "symlink_or_junction_not_followed",
                )
            )
            continue

        rel = _relative_name(workspace_root, source)
        if not rel:
            excluded.append(
                _excluded_detail("<outside-workspace>", "path_outside_workspace")
            )
            continue
        if source.resolve() == output_resolved:
            excluded.append(_excluded_detail(rel, "evidence_output_file"))
            continue

        artifact = artifact_map.get(rel, {})
        reason = _exclude_reason(rel)
        if reason:
            excluded.append(_excluded_detail(rel, reason, artifact, source))
            continue
        if not _is_allowed_artifact(rel):
            excluded.append(
                _excluded_detail(rel, "not_in_allowed_roots", artifact, source)
            )
            continue
        artifact_reason = _artifact_exclude_reason(rel, artifact)
        if artifact_reason:
            excluded.append(_excluded_detail(rel, artifact_reason, artifact, source))
            continue

        try:
            size = source.stat().st_size
        except OSError as exc:
            excluded.append(
                _excluded_detail(rel, f"stat_failed:{type(exc).__name__}", artifact)
            )
            continue
        if size > MAX_ENTRY_BYTES:
            excluded.append(
                _excluded_detail(rel, "entry_size_limit_exceeded", artifact, source)
            )
            continue

        try:
            content = source.read_bytes()
        except OSError as exc:
            excluded.append(
                _excluded_detail(rel, f"read_failed:{type(exc).__name__}", artifact)
            )
            continue

        entry, redaction_detail, warning = _entry_from_content(
            rel,
            content,
            artifact,
            context,
        )
        if warning:
            warnings.append(f"{rel}: {warning}")
        if entry is None:
            excluded.append(
                _excluded_detail(
                    rel, redaction_detail or "redaction_failed", artifact, source
                )
            )
            continue
        if len(entries) >= MAX_BUNDLE_FILES:
            excluded.append(
                _excluded_detail(rel, "bundle_file_count_limit_exceeded", artifact)
            )
            continue
        if total_bytes + len(entry.content) > MAX_BUNDLE_UNCOMPRESSED_BYTES:
            excluded.append(
                _excluded_detail(
                    rel, "bundle_uncompressed_size_limit_exceeded", artifact
                )
            )
            continue
        if _archive_path_conflicts(entries, entry.path):
            excluded.append(_excluded_detail(rel, "zip_entry_conflict", artifact))
            continue
        entries[entry.path] = entry
        total_bytes += len(entry.content)
        if entry.redacted:
            redacted_files.append(
                {
                    "path": entry.path,
                    "redaction_state": entry.redaction_state,
                    "encoding": entry.encoding,
                }
            )
    return entries, excluded, redacted_files, warnings


def _add_normalized_evidence(
    entries: dict[str, BundleEntry],
    excluded: list[dict[str, Any]],
    redacted_files: list[dict[str, Any]],
    warnings: list[str],
    workspace: Path,
    artifact_map: Mapping[str, Mapping[str, Any]],
    context: RedactionContext,
) -> None:
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
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    artifact = artifact_map.get(
        VIRTUAL_NORMALIZED_EVIDENCE,
        {
            "path": VIRTUAL_NORMALIZED_EVIDENCE,
            "category": "normalized_evidence",
            "include_in_evidence": True,
            "redaction_state": "redacted",
        },
    )
    entry, redaction_detail, warning = _entry_from_content(
        VIRTUAL_NORMALIZED_EVIDENCE,
        content,
        artifact,
        context,
    )
    if warning:
        warnings.append(f"{VIRTUAL_NORMALIZED_EVIDENCE}: {warning}")
    if entry is None:
        excluded.append(
            _excluded_detail(
                VIRTUAL_NORMALIZED_EVIDENCE,
                redaction_detail or "redaction_failed",
                artifact,
            )
        )
        return
    if _archive_path_conflicts(entries, entry.path):
        excluded.append(
            _excluded_detail(
                VIRTUAL_NORMALIZED_EVIDENCE,
                "zip_entry_conflict",
                artifact,
            )
        )
        return
    entries[entry.path] = entry
    if entry.redacted:
        redacted_files.append(
            {
                "path": entry.path,
                "redaction_state": entry.redaction_state,
                "encoding": entry.encoding,
            }
        )


def _entry_from_content(
    rel: str,
    content: bytes,
    artifact: Mapping[str, Any],
    context: RedactionContext,
) -> tuple[BundleEntry | None, str, str]:
    if not _is_text_format(rel):
        return None, "raw_binary_redaction_not_supported", ""
    try:
        result = redact_text_artifact(rel, content, context=context)
    except RedactionError as exc:
        return None, str(exc), ""
    return (
        BundleEntry(
            path=rel,
            content=result.content,
            category=str(artifact.get("category") or _category_for_entry(rel)),
            redaction_state="redacted",
            redacted=result.redacted,
            encoding=result.encoding,
        ),
        "",
        ";".join(result.warnings),
    )


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


def _artifact_exclude_reason(rel: str, artifact: Mapping[str, Any]) -> str:
    if artifact and not bool(artifact.get("include_in_evidence", True)):
        if str(artifact.get("category", "")) == "raw_product_log":
            return "raw_binary_redaction_not_supported"
        return str(artifact.get("redaction_state") or "collector_policy_excluded")
    if rel.startswith("collection/") and _is_raw_binary_like_path(rel):
        return "raw_binary_redaction_not_supported"
    return ""


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
    if lowered == "configs/real.toml":
        return "real_cloud_config"
    if lowered.startswith("configs/"):
        return "config_file"
    if name.endswith(SENSITIVE_SUFFIXES):
        return "sensitive_config_name"
    if any(marker in name for marker in SENSITIVE_NAME_MARKERS):
        return "suspected_secret_name"
    return ""


def _excluded_detail(
    rel: str,
    reason: str,
    artifact: Mapping[str, Any] | None = None,
    source: Path | None = None,
) -> dict[str, Any]:
    artifact = artifact or {}
    detail: dict[str, Any] = {
        "path": rel.replace("\\", "/"),
        "reason": reason,
        "category": str(artifact.get("category") or _category_for_entry(rel)),
        "redaction_state": str(artifact.get("redaction_state") or "not_included"),
        "sensitivity": str(artifact.get("sensitivity") or "unknown"),
        "source_trust": str(artifact.get("source_trust") or "guest_reported"),
    }
    if source is not None:
        _add_best_effort_source_metadata(detail, source)
    return detail


def _add_best_effort_source_metadata(detail: dict[str, Any], source: Path) -> None:
    try:
        size = source.stat().st_size
    except OSError:
        detail["source_sha256_available"] = False
        return
    detail["source_size"] = size
    if size > SOURCE_HASH_MAX_BYTES:
        detail["source_sha256_available"] = False
        detail["source_sha256_unavailable_reason"] = "source_hash_size_limit_exceeded"
        return
    try:
        detail["source_sha256"] = sha256_bytes(source.read_bytes())
        detail["source_sha256_available"] = True
    except OSError as exc:
        detail["source_sha256_available"] = False
        detail["source_sha256_unavailable_reason"] = type(exc).__name__


def _collector_artifact_map(
    collection: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    artifacts = collection.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return {}, []
    items = artifacts.get("items")
    if not isinstance(items, list):
        return {}, []
    mapped: dict[str, Mapping[str, Any]] = {}
    warnings: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path", "")).replace("\\", "/")
        if not _is_safe_zip_name(path):
            warnings.append("collector_artifact_unsafe_path_ignored")
            continue
        mapped[path] = dict(item)
    return mapped, warnings


def _redaction_context(
    workspace: Path, collection: Mapping[str, Any]
) -> RedactionContext:
    known_paths: dict[str, str] = {}
    artifacts = collection.get("artifacts")
    if isinstance(artifacts, Mapping):
        legacy = artifacts.get("legacy")
        if isinstance(legacy, Mapping):
            _collect_known_paths(legacy, known_paths)
    return RedactionContext(
        case_workspace=str(workspace.resolve()),
        known_paths=known_paths,
    )


def _collect_known_paths(value: Any, known_paths: dict[str, str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            placeholder = _placeholder_for_key(str(key))
            if isinstance(child, str) and _looks_like_absolute_path(child):
                known_paths[child] = placeholder
            else:
                _collect_known_paths(child, known_paths)
        return
    if isinstance(value, list):
        for child in value:
            _collect_known_paths(child, known_paths)
        return
    if isinstance(value, str) and _looks_like_absolute_path(value):
        known_paths[value] = "<collector_path>"


def _placeholder_for_key(key: str) -> str:
    lowered = key.casefold()
    if "source" in lowered:
        return "<collector_source>"
    if "artifact" in lowered:
        return "<collector_artifact>"
    return "<collector_path>"


def _looks_like_absolute_path(value: str) -> bool:
    normalized = value.replace("/", "\\")
    return (
        (len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "\\")
        or normalized.startswith("\\\\")
        or value.startswith("/")
    )


def _is_text_format(rel: str) -> bool:
    return Path(rel).suffix.casefold() in TEXT_SUFFIXES


def _is_raw_binary_like_path(rel: str) -> bool:
    return Path(rel).suffix.casefold() in RAW_BINARY_SUFFIXES


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
    if not name or "\x00" in name or "\\" in name or name.startswith(("/", "\\")):
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("../") or "/../" in normalized or normalized == "..":
        return False
    if len(normalized) >= 2 and normalized[1] == ":":
        return False
    return not normalized.startswith("//")


def _archive_path_conflicts(entries: Mapping[str, BundleEntry], name: str) -> bool:
    key = name.replace("\\", "/").casefold()
    return any(existing.replace("\\", "/").casefold() == key for existing in entries)


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


def _case_id_from_entries(entries: Mapping[str, BundleEntry]) -> str:
    for filename in ("case_summary.json", "case_report.json", "case_state.json"):
        payload = _decode_entry(entries.get(filename))
        case_id = payload.get("case_id")
        if case_id:
            return str(case_id)
    return ""


def _product_id_from_entries(entries: Mapping[str, BundleEntry]) -> str:
    summary = _decode_entry(entries.get("case_summary.json"))
    if summary.get("product_id"):
        return str(summary["product_id"])
    report = _decode_entry(entries.get("case_report.json"))
    if report.get("product_id"):
        return str(report["product_id"])
    collection = _decode_entry(entries.get("case_collection.json"))
    return str(collection.get("product_id", ""))


def _decode_entry(entry: BundleEntry | None) -> dict[str, Any]:
    if entry is None:
        return {}
    try:
        decoded = json.loads(entry.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
