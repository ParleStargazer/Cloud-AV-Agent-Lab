from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_VERSION = "evidence-bundle.v2"
COLLECTION_POLICY = "workspace-artifacts-allowlisted-excluding-sample-and-secrets"
REDACTION_POLICY = (
    "excludes uploaded sample bytes, tokens, cloud credentials, real cloud "
    "configs, environment dumps, recursive evidence zips, and suspected secret "
    "files; includes collector-produced artifacts under collection/"
)
EXCLUDED_PATHS = (
    "sample/",
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
) -> dict[str, Any]:
    concrete_excluded = sorted(set(excluded_paths or ()))
    policy_excluded = list(EXCLUDED_PATHS)
    return {
        "schema_version": SCHEMA_VERSION,
        "collection_policy": COLLECTION_POLICY,
        "case_id": case_id,
        "product_id": product_id,
        "generated_at_utc": generated_at_utc,
        "included_paths": list(included_paths or ()),
        "files": [dict(item) for item in files],
        "redaction_policy": REDACTION_POLICY,
        "excluded_paths": policy_excluded
        + [path for path in concrete_excluded if path not in policy_excluded],
        "excluded_path_details": [dict(item) for item in excluded_path_details or ()],
    }


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
