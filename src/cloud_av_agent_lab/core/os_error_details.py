from __future__ import annotations

import errno
from collections.abc import Mapping
from typing import Any


def safe_os_error_details(exc: OSError) -> dict[str, Any]:
    """Return non-path OSError metadata suitable for reports and API errors."""

    errno_value = _coerce_int(getattr(exc, "errno", None))
    winerror_value = _coerce_int(getattr(exc, "winerror", None))
    strerror = _safe_strerror(getattr(exc, "strerror", None) or _first_arg(exc))
    return {
        "type": type(exc).__name__,
        "reason_code": classify_os_error(errno_value, winerror_value),
        "errno": errno_value,
        "winerror": winerror_value,
        "strerror": strerror,
    }


def classify_os_error(
    errno_value: int | None,
    winerror_value: int | None,
) -> str:
    """Map common Windows process launch failures to safe reason codes."""

    if winerror_value == 2:
        return "file_not_found"
    if winerror_value == 3:
        return "path_not_found"
    if winerror_value == 5:
        return "permission_denied"
    if winerror_value == 32:
        return "sharing_violation"
    if winerror_value == 193:
        return "invalid_executable"
    if winerror_value == 216:
        return "unsupported_executable_architecture"
    if winerror_value == 225:
        return "blocked_by_security_product"
    if winerror_value == 740:
        return "elevation_required"
    if errno_value == errno.EACCES:
        return "permission_denied"
    if errno_value == errno.ENOENT:
        return "file_not_found"
    return "os_error"


def format_os_error_details(details: Mapping[str, Any]) -> str:
    """Format safe launch failure metadata without file paths."""

    parts = [str(details.get("type") or "OSError")]
    reason_code = str(details.get("reason_code") or "").strip()
    if reason_code:
        parts.append(f"reason_code={reason_code}")
    winerror_value = details.get("winerror")
    if winerror_value is not None:
        parts.append(f"winerror={winerror_value}")
    errno_value = details.get("errno")
    if errno_value is not None:
        parts.append(f"errno={errno_value}")
    strerror = str(details.get("strerror") or "").strip()
    if strerror:
        parts.append(f"message={strerror}")
    return " ".join(parts)


def _coerce_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _first_arg(exc: OSError) -> str:
    if not exc.args:
        return ""
    return str(exc.args[0])


def _safe_strerror(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 240:
        return text[:237] + "..."
    return text
