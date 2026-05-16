from __future__ import annotations

import json
import zipfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import build_manifest, sha256_bytes

EVIDENCE_BUNDLE_PREFIX = "case_evidence_"
METADATA_FILES = (
    "case_state.json",
    "case_report.json",
    "case_collection.json",
    "case_summary.json",
    "case_summary.md",
    "events.jsonl",
)


def build_evidence_bundle(
    workspace: Path,
    output_path: Path,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    entries = _metadata_entries(workspace)
    _add_normalized_evidence(entries, workspace)
    generated_at = _utc_now()
    files = [
        {
            "path": name,
            "size": len(content),
            "sha256": sha256_bytes(content),
        }
        for name, content in sorted(entries.items())
    ]
    manifest = build_manifest(
        case_id=_case_id_from_entries(entries),
        product_id=_product_id_from_entries(entries),
        generated_at_utc=generated_at,
        files=files,
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


def _metadata_entries(workspace: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    for filename in METADATA_FILES:
        source = workspace / filename
        if source.is_file():
            entries[filename] = source.read_bytes()
    return entries


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
    entries["collector/normalized_evidence.json"] = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


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
