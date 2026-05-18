from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any


class ExecutionLeaseError(RuntimeError):
    """Raised when an execution lease is malformed, expired, or mismatched."""


def issue_execution_lease(
    *,
    secret: str,
    case_id: str,
    sample_id: str,
    run_id: str,
    expected_sha256: str,
    ttl_seconds: float = 60.0,
) -> str:
    if not secret:
        raise ExecutionLeaseError("execution lease secret is not configured")
    now = datetime.now(timezone.utc)
    payload = {
        "case_id": str(case_id),
        "sample_id": str(sample_id),
        "run_id": str(run_id),
        "expected_sha256": str(expected_sha256),
        "expires_at_utc": (now + timedelta(seconds=ttl_seconds))
        .isoformat()
        .replace("+00:00", "Z"),
        "nonce": secrets.token_urlsafe(18),
    }
    payload_b64 = _b64encode(_canonical_json(payload))
    return f"{payload_b64}.{_signature(secret, payload_b64)}"


def verify_execution_lease(
    *,
    token: str,
    secret: str,
    expected: Mapping[str, str],
) -> dict[str, str]:
    if not secret:
        raise ExecutionLeaseError("execution lease secret is not configured")
    try:
        payload_b64, provided_signature = token.split(".", 1)
    except ValueError as exc:
        raise ExecutionLeaseError("execution lease is malformed") from exc

    expected_signature = _signature(secret, payload_b64)
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise ExecutionLeaseError("execution lease signature is invalid")

    try:
        decoded = json.loads(_b64decode(payload_b64).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExecutionLeaseError("execution lease payload is invalid") from exc
    if not isinstance(decoded, dict):
        raise ExecutionLeaseError("execution lease payload must be an object")

    payload = {str(key): str(value) for key, value in decoded.items()}
    expires_at = _parse_utc_timestamp(payload.get("expires_at_utc", ""))
    if expires_at is None:
        raise ExecutionLeaseError("execution lease expiry is invalid")
    if expires_at <= datetime.now(timezone.utc):
        raise ExecutionLeaseError("execution lease has expired")

    for key, expected_value in expected.items():
        if payload.get(key, "") != str(expected_value):
            raise ExecutionLeaseError(f"execution lease {key} does not match request")
    if not payload.get("nonce"):
        raise ExecutionLeaseError("execution lease nonce is missing")
    return payload


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _signature(secret: str, payload_b64: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64encode(digest)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _parse_utc_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        decoded = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if decoded.tzinfo is None:
        return decoded.replace(tzinfo=timezone.utc)
    return decoded.astimezone(timezone.utc)
