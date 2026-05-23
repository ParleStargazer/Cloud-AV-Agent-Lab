from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.guest_agent_server.collectors.windows_defender import (
    OPERATIONAL_CHANNEL,
    PRODUCT_ID,
    PROVIDER_NAME,
    WindowsDefenderParsedEvent,
    event_kind_for_id,
    parse_windows_defender_event_xml,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "windows_defender"


class WindowsDefenderParserTests(TestCase):
    def test_product_id_is_explicit(self) -> None:
        self.assertEqual(PRODUCT_ID, "windows-defender")

    def test_1116_detection_xml_parses_structured_fields(self) -> None:
        event = _parse_fixture("1116_detection.xml")

        self.assertIsInstance(event, WindowsDefenderParsedEvent)
        self.assertEqual(event.event_id, 1116)
        self.assertEqual(event.event_kind, "detected")
        self.assertEqual(event.provider, PROVIDER_NAME)
        self.assertEqual(event.channel, OPERATIONAL_CHANNEL)
        self.assertEqual(event.computer, "WIN-TEST")
        self.assertEqual(event.record_id, "12345")
        self.assertEqual(event.observed_at_utc, "2026-05-23T04:01:02.0000000Z")
        self.assertEqual(event.threat_name, "Virus:DOS/EICAR_Test_File")
        self.assertEqual(event.threat_id, "2147519003")
        self.assertEqual(event.severity, "Severe")
        self.assertEqual(event.category, "Virus")
        self.assertIn("eicar.txt", event.path or "")
        self.assertEqual(event.process_name, r"C:\Windows\explorer.exe")
        self.assertEqual(event.user, r"NT AUTHORITY\SYSTEM")
        self.assertEqual(event.action, "Not Applicable")
        self.assertEqual(event.action_status, "1")
        self.assertEqual(event.raw_event_data["Custom Field"], "kept as raw data")

    def test_1117_action_taken_xml_parses_action_fields(self) -> None:
        event = _parse_fixture("1117_action_taken.xml")

        self.assertEqual(event.event_id, 1117)
        self.assertEqual(event.event_kind, "action_taken")
        self.assertEqual(event.action, "Quarantine")
        self.assertEqual(event.action_status, "0")
        self.assertEqual(event.error_code, "0x00000000")
        self.assertEqual(
            event.error_description,
            "The operation completed successfully.",
        )

    def test_legacy_detection_and_action_event_ids_are_classified(self) -> None:
        detection = _parse_fixture("1006_detection.xml")
        action = _parse_fixture("1007_action_taken.xml")

        self.assertEqual(detection.event_kind, "detected")
        self.assertEqual(detection.process_name, r"C:\Windows\System32\notepad.exe")
        self.assertEqual(detection.user, r"AVLAB\tester")
        self.assertEqual(action.event_kind, "action_taken")
        self.assertEqual(action.action, "Remove")
        self.assertEqual(action.action_status, "Succeeded")

    def test_protection_state_event_ids_are_classified_without_verdict_semantics(
        self,
    ) -> None:
        enabled = _parse_fixture("5000_rtp_enabled.xml")
        disabled = _parse_fixture("5001_rtp_disabled.xml")

        self.assertEqual(enabled.event_kind, "protection_state_changed")
        self.assertEqual(disabled.event_kind, "protection_state_changed")
        self.assertEqual(enabled.raw_event_data["New State"], "Enabled")
        self.assertEqual(disabled.raw_event_data["New State"], "Disabled")

    def test_action_failed_event_ids_are_classified(self) -> None:
        first = _parse_fixture("1118_action_failed.xml")
        second = _parse_fixture("1119_action_critically_failed.xml")

        self.assertEqual(event_kind_for_id(1118), "action_failed")
        self.assertEqual(event_kind_for_id(1119), "action_failed")
        self.assertEqual(first.event_kind, "action_failed")
        self.assertEqual(first.action, "Quarantine")
        self.assertEqual(first.action_status, "Failed")
        self.assertEqual(first.error_code, "0x80070005")
        self.assertEqual(second.event_kind, "action_failed")
        self.assertEqual(second.action, "Remove")
        self.assertEqual(second.action_status, "Critically Failed")
        self.assertEqual(second.error_code, "0x80508023")

    def test_5011_scanning_enabled_is_protection_state_changed(self) -> None:
        event = _parse_fixture("5011_scanning_enabled.xml")

        self.assertEqual(event.event_id, 5011)
        self.assertEqual(event.event_kind, "protection_state_changed")
        self.assertEqual(event.raw_event_data["Feature Name"], "Antivirus Scanning")
        self.assertEqual(event.raw_event_data["New State"], "Enabled")

    def test_missing_fields_do_not_crash(self) -> None:
        event = parse_windows_defender_event_xml(
            """
            <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
              <System>
                <Provider Name="Microsoft-Windows-Windows Defender"/>
                <EventID>1116</EventID>
              </System>
              <EventData>
                <Data Name="Threat Name">Virus:DOS/EICAR_Test_File</Data>
              </EventData>
            </Event>
            """
        )

        self.assertEqual(event.event_id, 1116)
        self.assertEqual(event.event_kind, "detected")
        self.assertEqual(event.threat_name, "Virus:DOS/EICAR_Test_File")
        self.assertIsNone(event.channel)
        self.assertIsNone(event.path)

    def test_unknown_event_id_does_not_crash(self) -> None:
        event = parse_windows_defender_event_xml(
            """
            <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
              <System>
                <Provider Name="Microsoft-Windows-Windows Defender"/>
                <EventID>9999</EventID>
                <TimeCreated SystemTime="2026-05-23T04:05:00.0000000Z"/>
              </System>
              <EventData>
                <Data Name="Some Field">Some Value</Data>
              </EventData>
            </Event>
            """
        )

        self.assertEqual(event.event_id, 9999)
        self.assertEqual(event.event_kind, "unknown")
        self.assertEqual(event.raw_event_data["Some Field"], "Some Value")

    def test_duplicate_raw_event_data_is_preserved(self) -> None:
        event = parse_windows_defender_event_xml(
            """
            <Event>
              <System><EventID>1116</EventID></System>
              <EventData>
                <Data Name="Path">first</Data>
                <Data Name="Path">second</Data>
              </EventData>
            </Event>
            """
        )

        self.assertEqual(event.raw_event_data["Path"], "first")
        self.assertEqual(event.raw_event_data["Path#2"], "second")
        self.assertEqual(
            event.raw_event_data_items,
            (("Path", "first"), ("Path", "second")),
        )

    def test_general_message_text_is_not_required(self) -> None:
        event = parse_windows_defender_event_xml(
            """
            <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
              <System><EventID>1117</EventID></System>
              <RenderingInfo Culture="zh-CN">
                <Message>Localized prose is intentionally ignored.</Message>
              </RenderingInfo>
              <EventData>
                <Data Name="Threat Name">Virus:DOS/EICAR_Test_File</Data>
                <Data Name="Action Name">Quarantine</Data>
              </EventData>
            </Event>
            """
        )

        self.assertEqual(event.event_kind, "action_taken")
        self.assertEqual(event.threat_name, "Virus:DOS/EICAR_Test_File")
        self.assertEqual(event.action, "Quarantine")

    def test_malformed_xml_returns_unknown_event(self) -> None:
        event = parse_windows_defender_event_xml("<Event>")

        self.assertIsNone(event.event_id)
        self.assertEqual(event.event_kind, "unknown")


def _parse_fixture(name: str) -> WindowsDefenderParsedEvent:
    return parse_windows_defender_event_xml(
        (FIXTURE_DIR / name).read_text(encoding="utf-8")
    )
