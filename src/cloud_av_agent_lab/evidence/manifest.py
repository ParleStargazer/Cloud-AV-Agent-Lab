from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from .redaction import DEFAULT_MAX_TEXT_REDACTION_BYTES

SCHEMA_VERSION = "evidence-bundle.v2"
COLLECTION_POLICY = "redacted-text-artifacts-only-excluding-raw-binary"
MAX_BUNDLE_FILES = 200
MAX_ENTRY_BYTES = 10 * 1024 * 1024
MAX_BUNDLE_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
REDACTION_POLICY = {
    "schema_version": "evidence-redaction.v1",
    "enabled": True,
    "mode": "guest-reported-redacted-text",
    "text_files_redacted": True,
    "binary_files_redacted": False,
    "preserve_hashes": True,
    "max_bundle_files": MAX_BUNDLE_FILES,
    "max_entry_bytes": MAX_ENTRY_BYTES,
    "max_bundle_uncompressed_bytes": MAX_BUNDLE_UNCOMPRESSED_BYTES,
    "max_text_redaction_bytes": DEFAULT_MAX_TEXT_REDACTION_BYTES,
    "product_semantic_redaction_owner": "collector",
    "global_redaction_owner": "evidence_exporter",
    "text_formats": ["json", "jsonl", "md", "txt"],
    "global_redaction": [
        "case workspace paths",
        "collector source paths",
        "Windows user home paths",
        "bearer tokens",
        "cookies",
        "token/secret/credential/api_key-like fields",
    ],
    "preserved_fields": [
        "case_id",
        "sample_id",
        "run_id",
        "vm_id",
        "product_id",
        "hashes",
        "timestamps",
        "verdict",
        "detection names",
    ],
    "fail_closed": True,
    "raw_binary_default": "excluded",
}
EXCLUDED_PATHS = (
    "sample/",
    "security-product-readiness/",
    "configs/*.toml",
    "evidence/*.zip",
    "*.local.toml",
    "*.secret*.toml",
    "*.secrets*.toml",
    ".env",
    "token*",
    "*credential*",
    "*key*",
    ".worker_secret",
    "environment variables",
    "tokens",
    "cloud credentials",
    "configs/real.toml",
)


def build_manifest(
    case_id: str,
    product_id: str,
    generated_at_utc: str,
    files: Sequence[Mapping[str, Any]],
    included_paths: Sequence[str] | None = None,
    excluded_paths: Sequence[str] | None = None,
    excluded_path_details: Sequence[Mapping[str, Any]] | None = None,
    redacted_files: Sequence[Mapping[str, Any]] | None = None,
    redaction_warnings: Sequence[str] | None = None,
    trust_model: str = "dirty_instance_untrusted",
    source_trust: str = "guest_reported",
    forensic_grade: bool = False,
    raw_binary_included: bool = False,
    archive_format: str = "zip",
    password_protected: bool = False,
) -> dict[str, Any]:
    concrete_excluded = sorted(set(excluded_paths or ()))
    policy_excluded = list(EXCLUDED_PATHS)
    return {
        "schema_version": SCHEMA_VERSION,
        "collection_policy": COLLECTION_POLICY,
        "trust_model": trust_model,
        "source_trust": source_trust,
        "forensic_grade": forensic_grade,
        "generated_by": "dirty_guest_agent",
        "archive_format": archive_format,
        "password_protected": password_protected,
        "raw_binary_included": raw_binary_included,
        "case_id": case_id,
        "product_id": product_id,
        "generated_at_utc": generated_at_utc,
        "included_paths": list(included_paths or ()),
        "files": [dict(item) for item in files],
        "redaction_policy": REDACTION_POLICY,
        "redacted_files": [dict(item) for item in redacted_files or ()],
        "redaction_warnings": list(redaction_warnings or ()),
        "excluded_paths": policy_excluded
        + [path for path in concrete_excluded if path not in policy_excluded],
        "excluded_path_details": [dict(item) for item in excluded_path_details or ()],
    }


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
