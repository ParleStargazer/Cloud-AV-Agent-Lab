from __future__ import annotations

import platform
from collections.abc import Callable, Sequence

from cloud_av_agent_lab.guest_agent_server.collectors.windows_defender import (
    CORE_EVENT_IDS,
    OPERATIONAL_CHANNEL,
    PRODUCT_ID,
    WindowsEventLogAccessDenied,
    WindowsEventLogChannelNotFound,
    WindowsEventLogError,
    WindowsEventLogQueryFailed,
    WindowsEventLogReader,
    WindowsEventRecord,
)
from cloud_av_agent_lab.guest_agent_server.collectors.windows_defender.reader import (
    PyWin32WindowsEventLogReader,
)
from cloud_av_agent_lab.guest_agent_server.workspace.io import _utc_now

from .base import (
    SecurityProductReadinessCheck,
    SecurityProductReadinessContext,
    SecurityProductReadinessResult,
)

READINESS_SCOPE = "log_observability"
PROTECTION_STATE = "unknown"
DEFAULT_QUERY_LIMIT = 50


class WindowsDefenderSecurityProductReadinessProbe:
    product_id = PRODUCT_ID
    DEFAULT_READER_FACTORY = PyWin32WindowsEventLogReader

    def __init__(
        self,
        reader: WindowsEventLogReader | None = None,
        platform_provider: Callable[[], str] | None = None,
    ) -> None:
        self.reader = reader if reader is not None else self.DEFAULT_READER_FACTORY()
        self.platform_provider = platform_provider or platform.system

    def check(
        self,
        context: SecurityProductReadinessContext,
    ) -> SecurityProductReadinessResult:
        platform_name = str(self.platform_provider() or "")
        checks: list[SecurityProductReadinessCheck] = []
        warnings = [
            "Windows Defender readiness verifies log observability only; "
            "it does not prove real-time protection is enabled."
        ]

        if platform_name.casefold() != "windows":
            checks.append(
                SecurityProductReadinessCheck(
                    name="windows_platform_supported",
                    status="unsupported",
                    message="Windows Defender readiness is supported only on Windows",
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

        try:
            records = self.reader.query(
                channel=OPERATIONAL_CHANNEL,
                event_ids=tuple(sorted(CORE_EVENT_IDS)),
                limit=DEFAULT_QUERY_LIMIT,
            )
        except WindowsEventLogChannelNotFound:
            checks.append(
                SecurityProductReadinessCheck(
                    name="defender_operational_channel_query",
                    status="failed",
                    message="Defender Operational channel was not found",
                    data={"channel": OPERATIONAL_CHANNEL},
                )
            )
            return _result(
                state="not_ready",
                checks=checks,
                warnings=warnings,
                errors=["Defender Operational channel was not found"],
                reason_codes=["defender_operational_channel_missing"],
            )
        except WindowsEventLogAccessDenied:
            return _query_error_result(
                checks=checks,
                warnings=warnings,
                reason_code="access_denied",
                error_message="Defender Operational channel access was denied",
            )
        except WindowsEventLogQueryFailed:
            return _query_error_result(
                checks=checks,
                warnings=warnings,
                reason_code="query_failed",
                error_message="Defender Operational channel query failed",
            )
        except WindowsEventLogError:
            return _query_error_result(
                checks=checks,
                warnings=warnings,
                reason_code="reader_error",
                error_message="Windows Event Log reader failed",
            )
        except Exception:
            return _query_error_result(
                checks=checks,
                warnings=warnings,
                reason_code="reader_error",
                error_message="Windows Event Log reader raised an unexpected error",
            )

        if not _valid_records(records):
            checks.append(
                SecurityProductReadinessCheck(
                    name="defender_operational_channel_query",
                    status="failed",
                    message="Windows Event Log reader returned invalid records",
                    data={"channel": OPERATIONAL_CHANNEL},
                )
            )
            return _result(
                state="unknown",
                checks=checks,
                warnings=warnings,
                errors=["Windows Event Log reader returned invalid records"],
                reason_codes=["invalid_reader_result"],
            )

        record_count = len(records)
        core_record_count = sum(
            1 for record in records if record.event_id in CORE_EVENT_IDS
        )
        reason_codes: list[str] = []
        if record_count == 0:
            reason_codes.append("no_recent_activity")
        elif core_record_count == 0:
            reason_codes.append("no_core_defender_events_returned")

        checks.append(
            SecurityProductReadinessCheck(
                name="defender_operational_channel_query",
                status="ok",
                message="Defender Operational channel query succeeded",
                data={
                    "channel": OPERATIONAL_CHANNEL,
                    "record_count": record_count,
                    "core_record_count": core_record_count,
                    "recent_activity_observed": record_count > 0,
                    "query_limit": DEFAULT_QUERY_LIMIT,
                },
            )
        )
        if core_record_count == 0 and record_count > 0:
            warnings.append(
                "Defender Operational channel was queryable, but returned no core "
                "Defender AV event IDs."
            )
            return _result(
                state="partial",
                checks=checks,
                warnings=warnings,
                errors=[],
                reason_codes=reason_codes,
            )
        return _result(
            state="ready",
            checks=checks,
            warnings=warnings,
            errors=[],
            reason_codes=reason_codes,
        )


def _query_error_result(
    checks: list[SecurityProductReadinessCheck],
    warnings: list[str],
    reason_code: str,
    error_message: str,
) -> SecurityProductReadinessResult:
    checks.append(
        SecurityProductReadinessCheck(
            name="defender_operational_channel_query",
            status="failed",
            message=error_message,
            data={"channel": OPERATIONAL_CHANNEL},
        )
    )
    return _result(
        state="unknown",
        checks=checks,
        warnings=warnings,
        errors=[error_message],
        reason_codes=[reason_code],
    )


def _valid_records(records: object) -> bool:
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return False
    return all(isinstance(record, WindowsEventRecord) for record in records)


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
        warnings=tuple(warnings),
        errors=tuple(errors),
    )
