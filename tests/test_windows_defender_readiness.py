from __future__ import annotations

import inspect
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.guest_agent_server.collectors import (
    registry as collector_registry,
)
from cloud_av_agent_lab.guest_agent_server.collectors.windows_defender import (
    OPERATIONAL_CHANNEL,
    WindowsDefenderLogCollector,
    WindowsEventLogAccessDenied,
    WindowsEventLogChannelNotFound,
    WindowsEventLogQueryFailed,
    WindowsEventRecord,
)
from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
    SecurityProductReadinessContext,
)
from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
    registry as readiness_registry,
)
from cloud_av_agent_lab.guest_agent_server.security_product_readiness.windows_defender import (
    DEFAULT_QUERY_LIMIT,
    WindowsDefenderSecurityProductReadinessProbe,
)


class WindowsDefenderReadinessTests(TestCase):
    def test_fake_reader_normal_records_return_ready(self) -> None:
        reader = FakeWindowsEventLogReader(
            records=[
                WindowsEventRecord(
                    event_id=5000,
                    xml="<Event><System><EventID>5000</EventID></System></Event>",
                    observed_at_utc="2026-05-23T00:00:00Z",
                    record_id="1",
                )
            ]
        )

        result = _check(reader)
        payload = result.to_dict()

        self.assertEqual(result.state, "ready")
        self.assertEqual(payload["state"], "ready")
        self.assertEqual(payload["scope"], "log_observability")
        self.assertEqual(payload["protection_state"], "unknown")
        self.assertEqual(payload["reason_codes"], [])
        self.assertTrue(payload["checked_at_utc"])
        self.assertEqual(reader.calls[0]["channel"], OPERATIONAL_CHANNEL)
        self.assertIn(5000, reader.calls[0]["event_ids"])
        self.assertEqual(reader.calls[0]["limit"], DEFAULT_QUERY_LIMIT)

    def test_fake_reader_empty_records_return_ready_with_no_recent_activity(
        self,
    ) -> None:
        result = _check(FakeWindowsEventLogReader(records=[]))
        payload = result.to_dict()
        query_check = _check_payload(payload, "defender_operational_channel_query")

        self.assertEqual(result.state, "ready")
        self.assertIn("no_recent_activity", payload["reason_codes"])
        self.assertFalse(query_check["data"]["recent_activity_observed"])
        self.assertEqual(query_check["data"]["record_count"], 0)

    def test_fake_reader_channel_not_found_returns_not_ready(self) -> None:
        result = _check(
            FakeWindowsEventLogReader(error=WindowsEventLogChannelNotFound("missing"))
        )
        payload = result.to_dict()

        self.assertEqual(result.state, "not_ready")
        self.assertIn("defender_operational_channel_missing", payload["reason_codes"])
        self.assertIn("Defender Operational channel was not found", result.errors)

    def test_fake_reader_access_denied_or_query_failed_returns_unknown(self) -> None:
        cases = (
            (WindowsEventLogAccessDenied("denied"), "access_denied"),
            (WindowsEventLogQueryFailed("failed"), "query_failed"),
        )
        for error, reason_code in cases:
            with self.subTest(reason_code=reason_code):
                result = _check(FakeWindowsEventLogReader(error=error))
                payload = result.to_dict()

                self.assertEqual(result.state, "unknown")
                self.assertIn(reason_code, payload["reason_codes"])
                self.assertTrue(result.errors)

    def test_non_windows_platform_returns_unsupported_without_reader_call(self) -> None:
        reader = FakeWindowsEventLogReader(
            records=[WindowsEventRecord(5000, "<Event/>")]
        )

        result = _check(reader, platform_name="Linux")
        payload = result.to_dict()

        self.assertEqual(result.state, "unsupported")
        self.assertIn("non_windows_platform", payload["reason_codes"])
        self.assertEqual(reader.calls, [])

    def test_invalid_reader_result_returns_unknown(self) -> None:
        result = _check(FakeWindowsEventLogReader(records=["bad-record"]))
        payload = result.to_dict()

        self.assertEqual(result.state, "unknown")
        self.assertIn("invalid_reader_result", payload["reason_codes"])

    def test_non_core_records_return_partial(self) -> None:
        result = _check(
            FakeWindowsEventLogReader(
                records=[WindowsEventRecord(9999, "<Event><System/></Event>")]
            )
        )
        payload = result.to_dict()

        self.assertEqual(result.state, "partial")
        self.assertIn("no_core_defender_events_returned", payload["reason_codes"])
        self.assertEqual(result.protection_state, "unknown")

    def test_stage_three_registers_collector_but_not_readiness_endpoint(self) -> None:
        collector = collector_registry.get_product_log_collector("windows-defender")

        self.assertIsInstance(collector, WindowsDefenderLogCollector)
        self.assertIn(
            "windows-defender",
            collector_registry.supported_product_log_collectors(),
        )
        self.assertNotIn(
            "windows-defender",
            readiness_registry.SUPPORTED_SECURITY_PRODUCT_READINESS_PROBES,
        )

    def test_stage_three_reader_uses_no_shell_or_command_runner(self) -> None:
        from cloud_av_agent_lab.guest_agent_server.collectors.windows_defender import (
            reader as reader_module,
        )
        from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
            windows_defender as readiness_module,
        )

        source = inspect.getsource(reader_module) + inspect.getsource(readiness_module)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("Get-WinEvent", source)
        self.assertNotIn("wevtutil", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("cmd.exe", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn("ctypes", source)


class FakeWindowsEventLogReader:
    def __init__(
        self,
        records: Sequence[WindowsEventRecord] | Sequence[object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.records = list(records or [])
        self.error = error
        self.calls: list[dict[str, object]] = []

    def query(
        self,
        channel: str,
        event_ids: Sequence[int],
        start_time_utc: datetime | None = None,
        end_time_utc: datetime | None = None,
        limit: int = 50,
    ) -> Sequence[object]:
        self.calls.append(
            {
                "channel": channel,
                "event_ids": tuple(event_ids),
                "start_time_utc": start_time_utc,
                "end_time_utc": end_time_utc,
                "limit": limit,
            }
        )
        if self.error is not None:
            raise self.error
        return self.records


def _check(
    reader: FakeWindowsEventLogReader,
    platform_name: str = "Windows",
):
    probe = WindowsDefenderSecurityProductReadinessProbe(
        reader=reader,
        platform_provider=lambda: platform_name,
    )
    return probe.check(
        SecurityProductReadinessContext(
            product_id="windows-defender",
            workspace=Path("unused"),
        )
    )


def _check_payload(payload: dict[str, object], name: str) -> dict[str, object]:
    checks = payload["checks"]
    if not isinstance(checks, list):
        raise AssertionError("checks payload must be a list")
    for item in checks:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    raise AssertionError(f"check not found: {name}")
