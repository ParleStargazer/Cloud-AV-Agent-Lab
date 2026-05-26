from __future__ import annotations

import platform
from collections.abc import Callable
from pathlib import Path

from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
    PRODUCT_ID,
    SUMMARY_DATABASE_NAME,
    UNION_METADATA_NAME,
    Qihoo360SQLiteError,
    read_qihoo360_summary_database,
)
from cloud_av_agent_lab.guest_agent_server.workspace.io import _utc_now

from .base import (
    SecurityProductReadinessCheck,
    SecurityProductReadinessContext,
    SecurityProductReadinessResult,
)

READINESS_SCOPE = "log_observability"
PROTECTION_STATE = "unknown"
READINESS_WARNING = (
    "Qihoo 360 readiness verifies log observability only; it does not prove "
    "real-time protection is enabled."
)
SUMMARY_NOT_FOUND_MESSAGE = (
    "360safe.Summary.dat was not found. This may mean no 360 "
    "quarantine/detection summary has been created yet."
)


class Qihoo360SecurityProductReadinessProbe:
    product_id = PRODUCT_ID

    def __init__(
        self,
        platform_provider: Callable[[], str] | None = None,
    ) -> None:
        self.platform_provider = platform_provider or platform.system

    def check(
        self,
        context: SecurityProductReadinessContext,
    ) -> SecurityProductReadinessResult:
        platform_name = str(self.platform_provider() or "")
        checks: list[SecurityProductReadinessCheck] = []
        warnings: list[str] = [READINESS_WARNING]
        reason_codes: list[str] = []

        if platform_name.casefold() != "windows":
            checks.append(
                SecurityProductReadinessCheck(
                    name="windows_platform_supported",
                    status="unsupported",
                    message="Qihoo 360 readiness is supported only on Windows",
                    data={"platform": platform_name or "unknown"},
                )
            )
            return _result(
                state="unsupported",
                checks=checks,
                warnings=warnings,
                errors=[],
                reason_codes=["non_windows_platform"],
            )

        checks.append(
            SecurityProductReadinessCheck(
                name="windows_platform_supported",
                status="ok",
                message="Windows platform detected",
                data={"platform": "Windows"},
            )
        )

        summary_path = _resolve_summary_path(context.log_dir)
        if summary_path is None:
            checks.append(
                SecurityProductReadinessCheck(
                    name="qihoo360_summary_dat_discovered",
                    status="failed",
                    message=SUMMARY_NOT_FOUND_MESSAGE,
                    data={"filename": SUMMARY_DATABASE_NAME},
                )
            )
            return _result(
                state="not_ready",
                checks=checks,
                warnings=warnings,
                errors=[SUMMARY_NOT_FOUND_MESSAGE],
                reason_codes=["summary_dat_not_found"],
            )

        checks.append(
            SecurityProductReadinessCheck(
                name="qihoo360_summary_dat_discovered",
                status="ok",
                message="360safe Summary.dat was found",
                data={"filename": SUMMARY_DATABASE_NAME},
            )
        )

        try:
            summary = read_qihoo360_summary_database(summary_path)
        except Qihoo360SQLiteError as exc:
            checks.append(
                SecurityProductReadinessCheck(
                    name="qihoo360_summary_dat_sqlite_readable",
                    status="failed",
                    message="360safe Summary.dat could not be queried",
                    data={"error": type(exc).__name__},
                )
            )
            return _result(
                state="unknown",
                checks=checks,
                warnings=warnings,
                errors=[_safe_error_message(exc)],
                reason_codes=["summary_dat_query_failed"],
            )
        except OSError as exc:
            checks.append(
                SecurityProductReadinessCheck(
                    name="qihoo360_summary_dat_sqlite_readable",
                    status="failed",
                    message="360safe Summary.dat metadata could not be read",
                    data={"error": type(exc).__name__},
                )
            )
            return _result(
                state="unknown",
                checks=checks,
                warnings=warnings,
                errors=[f"360safe Summary.dat access failed: {type(exc).__name__}"],
                reason_codes=["summary_dat_access_failed"],
            )

        checks.append(
            SecurityProductReadinessCheck(
                name="qihoo360_summary_dat_sqlite_readable",
                status="ok",
                message="360safe Summary.dat SQLite was readable",
                data={"filename": SUMMARY_DATABASE_NAME},
            )
        )
        checks.append(
            SecurityProductReadinessCheck(
                name="qihoo360_summary_dat_schema_verified",
                status="ok",
                message="360safe Summary.dat core tables were queryable",
                data={
                    "tables": list(summary.table_names),
                    "fi_record_count": len(summary.file_index),
                    "fq_record_count": len(summary.events),
                },
            )
        )

        if not summary.events:
            warnings.append("summary_records_empty")
            reason_codes.append("summary_records_empty")

        union_path = summary_path.with_name(UNION_METADATA_NAME)
        union_exists = union_path.is_file()
        checks.append(
            SecurityProductReadinessCheck(
                name="qihoo360_union_metadata_observed",
                status="ok",
                message=(
                    "360safe union metadata was observed"
                    if union_exists
                    else "Optional 360safe union metadata was not found"
                ),
                data={"filename": UNION_METADATA_NAME, "visible": union_exists},
            )
        )
        if not union_exists:
            warnings.append("union_metadata_missing")
            reason_codes.append("union_metadata_missing")

        return _result(
            state="ready",
            checks=checks,
            warnings=warnings,
            errors=[],
            reason_codes=reason_codes,
        )


def _resolve_summary_path(configured: Path | None) -> Path | None:
    candidates = []
    if configured is not None:
        configured_path = Path(configured)
        if configured_path.name.casefold() == SUMMARY_DATABASE_NAME.casefold():
            candidates.append(configured_path)
        else:
            candidates.append(configured_path / SUMMARY_DATABASE_NAME)
    else:
        candidates.extend(_default_summary_candidates())

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            return candidate
    return None


def _default_summary_candidates() -> tuple[Path, ...]:
    users_root = Path(r"C:\Users")
    candidates: list[Path] = []
    try:
        user_dirs = [path for path in users_root.iterdir() if path.is_dir()]
    except OSError:
        user_dirs = []
    for user_dir in user_dirs:
        candidates.append(
            user_dir / "AppData" / "Roaming" / "360Quarant" / SUMMARY_DATABASE_NAME
        )
    return tuple(candidates)


def _result(
    state: str,
    checks: list[SecurityProductReadinessCheck],
    warnings: list[str],
    errors: list[str],
    reason_codes: list[str],
) -> SecurityProductReadinessResult:
    confidence = "medium" if state in {"ready", "partial"} else "low"
    return SecurityProductReadinessResult(
        product_id=PRODUCT_ID,
        state=state,
        confidence=confidence,
        scope=READINESS_SCOPE,
        protection_state=PROTECTION_STATE,
        checked_at_utc=_utc_now(),
        reason_codes=tuple(reason_codes),
        checks=tuple(checks),
        warnings=tuple(dict.fromkeys(warnings)),
        errors=tuple(errors),
    )


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return message[:300] or type(exc).__name__
