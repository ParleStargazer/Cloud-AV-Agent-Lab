from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_VERSION = "evidence-bundle.v1"
REDACTION_POLICY = (
    "metadata-only: excludes uploaded sample files, tokens, cloud credentials, "
    "environment variables, real cloud configuration files, and raw collector "
    "database copies"
)
EXCLUDED_PATHS = (
    "sample/",
    "configs/*.toml",
    "environment variables",
    "tokens",
    "cloud credentials",
    "collection/*/log.db",
    "collection/*/log.db-shm",
    "collection/*/log.db-wal",
)


def build_manifest(
    case_id: str,
    product_id: str,
    generated_at_utc: str,
    files: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "product_id": product_id,
        "generated_at_utc": generated_at_utc,
        "files": [dict(item) for item in files],
        "redaction_policy": REDACTION_POLICY,
        "excluded_paths": list(EXCLUDED_PATHS),
    }


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
