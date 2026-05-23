from __future__ import annotations

import builtins
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.guest_agent_server.collectors import CollectionWindow
from cloud_av_agent_lab.guest_agent_server.collectors.windows_defender import (
    OPERATIONAL_CHANNEL,
    WindowsEventLogAccessDenied,
    WindowsEventLogChannelNotFound,
    WindowsEventLogQueryFailed,
    WindowsEventRecord,
)
from cloud_av_agent_lab.guest_agent_server.collectors.windows_defender.collector import (
    WindowsDefenderLogCollector,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "windows_defender"


class WindowsDefenderCollectorTests(TestCase):
    def test_strong_action_taken_yields_intercepted_event(self) -> None:
        collector = WindowsDefenderLogCollector(
            reader=FakeWindowsEventLogReader(
                [record_from_fixture("1117_action_taken.xml")]
            )
        )

        result = collector.collect(Path("unused"), _case_context(), _window())

        self.assertEqual(result.collection_state, "collected")
        self.assertEqual(result.verdict, "intercepted")
        self.assertTrue(result.intercepted)
        self.assertEqual(result.evidence_count, 1)
        event = result.events[0]
        self.assertEqual(event.event_type, "av_quarantined")
        self.assertEqual(event.confidence, "high")
        self.assertEqual(event.evidence["attribution"], "strong")
        self.assertTrue(event.evidence["time_window_matched"])
        self.assertTrue(event.evidence["path_matched"])
        self.assertIn("path", event.evidence["matched_on"])

    def test_strong_detection_yields_detected_not_remediated(self) -> None:
        collector = WindowsDefenderLogCollector(
            reader=FakeWindowsEventLogReader(
                [record_from_fixture("1116_detection.xml")]
            )
        )

        result = collector.collect(Path("unused"), _case_context(), _window())

        self.assertEqual(result.verdict, "detected")
        self.assertFalse(result.intercepted)
        self.assertEqual(result.events[0].event_type, "av_detected")
        self.assertEqual(result.events[0].evidence["attribution"], "strong")

    def test_allow_action_is_not_blocked_or_intercepted(self) -> None:
        collector = WindowsDefenderLogCollector(
            reader=FakeWindowsEventLogReader([record_from_xml(_action_xml("Allow"))])
        )

        result = collector.collect(Path("unused"), _case_context(), _window())

        self.assertEqual(result.verdict, "detected_only")
        self.assertFalse(result.intercepted)
        self.assertEqual(result.events[0].event_type, "av_detected_allowed")

    def test_action_failed_is_not_blocked_or_intercepted(self) -> None:
        collector = WindowsDefenderLogCollector(
            reader=FakeWindowsEventLogReader(
                [record_from_fixture("1118_action_failed.xml")]
            )
        )

        result = collector.collect(Path("unused"), _case_context(), _window())

        self.assertEqual(result.verdict, "detected_with_action_failed")
        self.assertIsNone(result.intercepted)
        self.assertEqual(result.events[0].event_type, "av_action_failed")

    def test_medium_pid_attribution_can_affect_verdict(self) -> None:
        collector = WindowsDefenderLogCollector(
            reader=FakeWindowsEventLogReader([record_from_xml(_pid_xml("4321"))])
        )

        result = collector.collect(Path("unused"), _case_context(), _window())

        self.assertEqual(result.verdict, "detected")
        self.assertEqual(result.events[0].confidence, "medium")
        self.assertEqual(result.events[0].evidence["attribution"], "medium")
        self.assertTrue(result.events[0].evidence["pid_matched"])
        self.assertIn("pid", result.events[0].evidence["matched_on"])

    def test_weak_eicar_event_does_not_create_confident_verdict(self) -> None:
        collector = WindowsDefenderLogCollector(
            reader=FakeWindowsEventLogReader([record_from_xml(_threat_only_xml())])
        )

        result = collector.collect(Path("unused"), _case_context(), _window())

        self.assertEqual(result.collection_state, "collected")
        self.assertEqual(result.verdict, "unknown")
        self.assertIsNone(result.intercepted)
        self.assertEqual(result.evidence_count, 1)
        self.assertEqual(result.events[0].evidence["attribution"], "weak")

    def test_out_of_window_event_is_unattributed(self) -> None:
        collector = WindowsDefenderLogCollector(
            reader=FakeWindowsEventLogReader(
                [record_from_fixture("1117_action_taken.xml")]
            )
        )

        result = collector.collect(
            Path("unused"),
            _case_context(),
            CollectionWindow(
                start_utc="2026-05-23T05:00:00Z",
                end_utc="2026-05-23T05:10:00Z",
            ),
        )

        self.assertEqual(result.verdict, "unknown")
        self.assertEqual(result.events[0].evidence["attribution"], "unattributed")
        self.assertFalse(result.events[0].evidence["time_window_matched"])

    def test_reader_failure_returns_failed_unknown(self) -> None:
        collector = WindowsDefenderLogCollector(
            reader=FakeWindowsEventLogReader(error=WindowsEventLogQueryFailed("boom"))
        )

        result = collector.collect(Path("unused"), _case_context(), _window())

        self.assertEqual(result.collection_state, "failed")
        self.assertEqual(result.verdict, "unknown")
        self.assertIsNone(result.intercepted)
        self.assertIn("query failed", " ".join(result.errors).casefold())
        self.assertIn("boom", " ".join(result.errors))

    def test_channel_and_access_errors_return_structured_failures(self) -> None:
        cases = (
            (
                WindowsEventLogChannelNotFound("missing"),
                "channel was not found",
            ),
            (
                WindowsEventLogAccessDenied("denied"),
                "access was denied",
            ),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                collector = WindowsDefenderLogCollector(
                    reader=FakeWindowsEventLogReader(error=error)
                )

                result = collector.collect(Path("unused"), _case_context(), _window())

                self.assertEqual(result.collection_state, "failed")
                self.assertEqual(result.verdict, "unknown")
                self.assertIsNone(result.intercepted)
                self.assertIn(expected, " ".join(result.errors))

    def test_default_reader_missing_pywin32_returns_structured_failure(self) -> None:
        original_import = builtins.__import__

        def missing_pywin32(
            name: str,
            globals: object | None = None,
            locals: object | None = None,
            fromlist: tuple[object, ...] = (),
            level: int = 0,
        ) -> object:
            if name in {"pywintypes", "win32evtlog"}:
                raise ImportError("missing optional pywin32")
            return original_import(name, globals, locals, fromlist, level)

        collector = WindowsDefenderLogCollector()
        with (
            patch(
                "cloud_av_agent_lab.guest_agent_server.collectors."
                "windows_defender.reader.platform.system",
                return_value="Windows",
            ),
            patch("builtins.__import__", side_effect=missing_pywin32),
        ):
            result = collector.collect(Path("unused"), _case_context(), _window())

        self.assertEqual(result.collection_state, "failed")
        self.assertEqual(result.verdict, "unknown")
        self.assertIsNone(result.intercepted)
        self.assertIn("pywin32 is required", " ".join(result.errors))

    def test_default_reader_non_windows_returns_structured_failure(self) -> None:
        collector = WindowsDefenderLogCollector()
        with patch(
            "cloud_av_agent_lab.guest_agent_server.collectors."
            "windows_defender.reader.platform.system",
            return_value="Linux",
        ):
            result = collector.collect(Path("unused"), _case_context(), _window())

        self.assertEqual(result.collection_state, "failed")
        self.assertEqual(result.verdict, "unknown")
        self.assertIsNone(result.intercepted)
        self.assertIn("only available on Windows", " ".join(result.errors))

    def test_collector_queries_operational_channel_and_window(self) -> None:
        reader = FakeWindowsEventLogReader([record_from_fixture("1116_detection.xml")])
        collector = WindowsDefenderLogCollector(reader=reader)

        collector.collect(Path("unused"), _case_context(), _window())

        call = reader.calls[0]
        self.assertEqual(call["channel"], OPERATIONAL_CHANNEL)
        self.assertIn(1116, call["event_ids"])
        self.assertIn(1117, call["event_ids"])
        self.assertEqual(
            call["start_time_utc"].isoformat(), "2026-05-23T04:00:00+00:00"
        )
        self.assertEqual(call["end_time_utc"].isoformat(), "2026-05-23T04:10:00+00:00")

    def test_artifacts_do_not_include_raw_evtx(self) -> None:
        collector = WindowsDefenderLogCollector(
            reader=FakeWindowsEventLogReader(
                [record_from_fixture("1116_detection.xml")]
            )
        )

        result = collector.collect(Path("unused"), _case_context(), _window())
        artifacts = result.to_dict()["artifacts"]
        paths = [item["path"] for item in artifacts["items"]]

        self.assertEqual(paths, ["collector/normalized_evidence.json"])
        self.assertFalse(result.artifacts["raw_event_log_included"])


class FakeWindowsEventLogReader:
    def __init__(
        self,
        records: Sequence[WindowsEventRecord] | None = None,
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
    ) -> Sequence[WindowsEventRecord]:
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
        return tuple(self.records)


def record_from_fixture(name: str) -> WindowsEventRecord:
    xml = (FIXTURE_DIR / name).read_text(encoding="utf-8")
    event_id = int(name.split("_", maxsplit=1)[0])
    return WindowsEventRecord(event_id=event_id, xml=xml)


def record_from_xml(xml: str) -> WindowsEventRecord:
    return WindowsEventRecord(event_id=None, xml=xml)


def _window() -> CollectionWindow:
    return CollectionWindow(
        start_utc="2026-05-23T04:00:00Z",
        end_utc="2026-05-23T04:10:00Z",
        uploaded_at_utc="2026-05-23T04:01:00Z",
        collection_started_at_utc="2026-05-23T04:10:00Z",
        collection_finished_at_utc="2026-05-23T04:10:00Z",
    )


def _case_context() -> dict[str, object]:
    return {
        "case_id": "eicar__windows-defender",
        "sample_id": "eicar",
        "sample_sha256": "0" * 64,
        "sample_dir": r"C:\CloudAvAgentLab\cases\eicar__windows-defender\sample",
        "stored_filename": "eicar.txt",
        "original_filename": "eicar.txt",
        "root_pid": 4321,
        "child_pids": [9876],
    }


def _action_xml(action: str) -> str:
    return f"""
    <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
      <System>
        <EventID>1117</EventID>
        <TimeCreated SystemTime="2026-05-23T04:01:05.0000000Z"/>
        <EventRecordID>action-{action}</EventRecordID>
        <Channel>Microsoft-Windows-Windows Defender/Operational</Channel>
      </System>
      <EventData>
        <Data Name="Threat Name">Virus:DOS/EICAR_Test_File</Data>
        <Data Name="Path">file:_C:\\CloudAvAgentLab\\cases\\eicar__windows-defender\\sample\\eicar.txt</Data>
        <Data Name="Action Name">{action}</Data>
      </EventData>
    </Event>
    """


def _pid_xml(pid: str) -> str:
    return f"""
    <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
      <System>
        <EventID>1116</EventID>
        <TimeCreated SystemTime="2026-05-23T04:01:02.0000000Z"/>
        <EventRecordID>pid-match</EventRecordID>
      </System>
      <EventData>
        <Data Name="Threat Name">Virus:DOS/EICAR_Test_File</Data>
        <Data Name="Process ID">{pid}</Data>
      </EventData>
    </Event>
    """


def _threat_only_xml() -> str:
    return """
    <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
      <System>
        <EventID>1116</EventID>
        <TimeCreated SystemTime="2026-05-23T04:01:02.0000000Z"/>
        <EventRecordID>weak-match</EventRecordID>
      </System>
      <EventData>
        <Data Name="Threat Name">Virus:DOS/EICAR_Test_File</Data>
      </EventData>
    </Event>
    """
