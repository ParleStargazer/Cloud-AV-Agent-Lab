from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cloud_av_agent_lab.guest_agent_server.collectors.base import (
    CollectionWindow,
    CollectorArtifact,
    CollectorResult,
    NormalizedSecurityEvent,
    ProductLogCollector,
)
from cloud_av_agent_lab.guest_agent_server.workspace.io import _utc_now

from .event_ids import (
    ACTION_FAILED_EVENT_IDS,
    ACTION_TAKEN_EVENT_IDS,
    DETECTION_EVENT_IDS,
)
from .parser import parse_windows_defender_event_xml
from .reader import (
    PyWin32WindowsEventLogReader,
    WindowsEventLogAccessDenied,
    WindowsEventLogChannelNotFound,
    WindowsEventLogError,
    WindowsEventLogQueryFailed,
    WindowsEventLogReader,
    WindowsEventRecord,
)
from .schema import OPERATIONAL_CHANNEL, PRODUCT_ID, PRODUCT_LOG_SOURCE

COLLECTION_EVENT_IDS = frozenset(
    DETECTION_EVENT_IDS | ACTION_TAKEN_EVENT_IDS | ACTION_FAILED_EVENT_IDS
)
CONFIDENT_ATTRIBUTIONS = {"strong", "medium"}
REMEDIATION_ACTION_KEYWORDS = (
    "clean",
    "quarantine",
    "remove",
    "delete",
    "block",
    "disinfect",
)
ALLOW_ACTION_KEYWORDS = ("allow", "no action", "none")


class WindowsDefenderLogCollector(ProductLogCollector):
    product_id = PRODUCT_ID
    DEFAULT_READER_FACTORY = PyWin32WindowsEventLogReader

    def __init__(self, reader: WindowsEventLogReader | None = None) -> None:
        self.reader = reader if reader is not None else self.DEFAULT_READER_FACTORY()

    def collect(
        self,
        workspace: Path,
        case_context: Mapping[str, Any],
        window: CollectionWindow,
    ) -> CollectorResult:
        errors: list[str] = []
        records = self._query_records(window, errors)
        events = [
            event
            for record in records
            if (
                event := _normalize_record(
                    record=record,
                    case_context=case_context,
                    window=window,
                )
            )
            is not None
        ]

        verdict, intercepted, reason = _verdict_from_events_and_errors(events, errors)
        if errors and not events:
            collection_state = "failed"
        elif errors:
            collection_state = "partial"
        else:
            collection_state = "collected"
        return CollectorResult(
            product_id=PRODUCT_ID,
            collection_state=collection_state,
            verdict=verdict,
            intercepted=intercepted,
            reason=reason,
            evidence_count=len(events),
            events=tuple(events),
            errors=tuple(errors),
            artifacts={
                "source_channel": OPERATIONAL_CHANNEL,
                "raw_event_log_included": False,
            },
            artifact_items=_windows_defender_artifacts(),
            window=window,
            collected_at_utc=_utc_now(),
        )

    def _query_records(
        self,
        window: CollectionWindow,
        errors: list[str],
    ) -> Sequence[WindowsEventRecord]:
        try:
            return self.reader.query(
                channel=OPERATIONAL_CHANNEL,
                event_ids=tuple(sorted(COLLECTION_EVENT_IDS)),
                start_time_utc=_parse_utc(window.start_utc),
                end_time_utc=_parse_utc(window.end_utc),
                limit=200,
            )
        except WindowsEventLogChannelNotFound:
            errors.append("Defender Operational channel was not found")
        except WindowsEventLogAccessDenied:
            errors.append("Defender Operational channel access was denied")
        except WindowsEventLogQueryFailed as exc:
            errors.append(f"Defender Operational channel query failed: {exc}")
        except WindowsEventLogError:
            errors.append("Windows Event Log reader failed")
        except Exception:
            errors.append("Windows Event Log reader raised an unexpected error")
        return ()


def _normalize_record(
    record: WindowsEventRecord,
    case_context: Mapping[str, Any],
    window: CollectionWindow,
) -> NormalizedSecurityEvent | None:
    parsed = parse_windows_defender_event_xml(record.xml)
    if parsed.event_id not in COLLECTION_EVENT_IDS:
        return None
    observed_at_utc = parsed.observed_at_utc or record.observed_at_utc or ""
    attribution = _attribute_event(
        parsed.to_dict(), case_context, observed_at_utc, window
    )
    event_type = _event_type(parsed.event_id, parsed.event_kind, parsed.action)
    if event_type == "ignore":
        return None
    evidence = {
        "event_id": parsed.event_id,
        "event_kind": parsed.event_kind,
        "record_id": parsed.record_id or record.record_id or "",
        "channel": parsed.channel or OPERATIONAL_CHANNEL,
        "computer": parsed.computer or "",
        "threat_name": parsed.threat_name or "",
        "threat_id": parsed.threat_id or "",
        "severity": parsed.severity or "",
        "category": parsed.category or "",
        "path": parsed.path or "",
        "process_name": parsed.process_name or "",
        "process_id": _process_id(parsed.raw_event_data),
        "user": parsed.user or "",
        "action": parsed.action or "",
        "action_status": parsed.action_status or "",
        "error_code": parsed.error_code or "",
        "error_description": parsed.error_description or "",
        "attribution": attribution["level"],
        "matched_on": attribution["matched_on"],
        "missing_fields": attribution["missing_fields"],
        "time_window_matched": attribution["time_window_matched"],
        "path_matched": attribution["path_matched"],
        "pid_matched": attribution["pid_matched"],
        "process_name_matched": attribution["process_name_matched"],
    }
    return NormalizedSecurityEvent(
        timestamp_utc=observed_at_utc or window.collection_started_at_utc,
        source=PRODUCT_LOG_SOURCE,
        event_type=event_type,
        case_id=str(case_context.get("case_id", "")),
        sample_id=str(case_context.get("sample_id", "")),
        product_id=PRODUCT_ID,
        confidence=_confidence_for_attribution(str(attribution["level"])),
        message=_message_for_event(
            parsed.event_kind, parsed.action, parsed.threat_name
        ),
        evidence=evidence,
        raw_ref=f"{OPERATIONAL_CHANNEL}#{evidence['record_id']}",
    )


def _attribute_event(
    parsed: Mapping[str, Any],
    case_context: Mapping[str, Any],
    observed_at_utc: str,
    window: CollectionWindow,
) -> dict[str, Any]:
    missing_fields: list[str] = []
    matched_on: list[str] = []
    time_window_matched = _timestamp_in_window(observed_at_utc, window)
    if not observed_at_utc:
        missing_fields.append("observed_at_utc")

    path = str(parsed.get("path") or "")
    process_name = str(parsed.get("process_name") or "")
    process_id = _process_id(parsed.get("raw_event_data"))
    threat_name = str(parsed.get("threat_name") or "")

    if not path:
        missing_fields.append("path")
    if not process_name:
        missing_fields.append("process_name")
    if not process_id:
        missing_fields.append("process_id")
    if not threat_name:
        missing_fields.append("threat_name")

    path_matched = _path_matches(path, case_context)
    process_name_matched = _process_name_matches(process_name, case_context)
    pid_matched = _pid_matches(process_id, case_context)
    weak_threat_match = bool(threat_name and "eicar" in threat_name.casefold())

    if time_window_matched and path_matched:
        matched_on.extend(["time_window", "path"])
        level = "strong"
    elif time_window_matched and (pid_matched or process_name_matched):
        matched_on.append("time_window")
        if pid_matched:
            matched_on.append("pid")
        if process_name_matched:
            matched_on.append("process_name")
        level = "medium"
    elif time_window_matched and weak_threat_match:
        matched_on.extend(["time_window", "threat_name"])
        level = "weak"
    else:
        level = "unattributed"

    return {
        "level": level,
        "matched_on": matched_on,
        "missing_fields": missing_fields,
        "time_window_matched": time_window_matched,
        "path_matched": path_matched,
        "pid_matched": pid_matched,
        "process_name_matched": process_name_matched,
    }


def _event_type(event_id: int | None, event_kind: str, action: str | None) -> str:
    if event_id in DETECTION_EVENT_IDS or event_kind == "detected":
        return "av_detected"
    if event_id in ACTION_FAILED_EVENT_IDS or event_kind == "action_failed":
        return "av_action_failed"
    if event_id in ACTION_TAKEN_EVENT_IDS or event_kind == "action_taken":
        action_text = str(action or "").casefold()
        if any(keyword in action_text for keyword in ALLOW_ACTION_KEYWORDS):
            return "av_detected_allowed"
        if "quarantine" in action_text:
            return "av_quarantined"
        if any(keyword in action_text for keyword in ("remove", "delete")):
            return "av_deleted"
        if "block" in action_text:
            return "av_blocked"
        if "clean" in action_text or "disinfect" in action_text:
            return "av_cleaned"
        return "av_action_taken"
    return "ignore"


def _verdict_from_events_and_errors(
    events: Sequence[NormalizedSecurityEvent],
    errors: Sequence[str],
) -> tuple[str, bool | None, str]:
    confident_events = [
        event
        for event in events
        if str(event.evidence.get("attribution")) in CONFIDENT_ATTRIBUTIONS
    ]
    remediated_events = [
        event
        for event in confident_events
        if event.event_type
        in {
            "av_quarantined",
            "av_deleted",
            "av_blocked",
            "av_cleaned",
            "av_action_taken",
        }
        and _is_remediation_action(str(event.evidence.get("action") or ""))
    ]
    if remediated_events:
        return "intercepted", True, "defender_remediation_evidence_matched_case"
    detected_events = [
        event for event in confident_events if event.event_type == "av_detected"
    ]
    if detected_events:
        return "detected", False, "defender_detection_evidence_matched_case"
    allowed_events = [
        event for event in confident_events if event.event_type == "av_detected_allowed"
    ]
    if allowed_events:
        return "detected_only", False, "defender_detected_but_allowed"
    failed_events = [
        event for event in confident_events if event.event_type == "av_action_failed"
    ]
    if failed_events:
        return (
            "detected_with_action_failed",
            None,
            "defender_detection_action_failed",
        )
    if events:
        return "unknown", None, "only_weak_or_unattributed_defender_events"
    if errors:
        return "unknown", None, "collection_failed_or_incomplete"
    return "not_intercepted", False, "no_defender_product_log_evidence_in_window"


def _is_remediation_action(action: str) -> bool:
    action_text = action.casefold()
    return any(
        keyword in action_text for keyword in REMEDIATION_ACTION_KEYWORDS
    ) and not any(keyword in action_text for keyword in ALLOW_ACTION_KEYWORDS)


def _message_for_event(
    event_kind: str,
    action: str | None,
    threat_name: str | None,
) -> str:
    threat = threat_name or "unknown threat"
    if event_kind == "action_taken":
        return f"Windows Defender action recorded for {threat}: {action or 'unknown'}"
    if event_kind == "action_failed":
        return f"Windows Defender action failed for {threat}"
    return f"Windows Defender detection recorded for {threat}"


def _confidence_for_attribution(attribution: str) -> str:
    return {
        "strong": "high",
        "medium": "medium",
        "weak": "low",
    }.get(attribution, "low")


def _path_matches(path: str, case_context: Mapping[str, Any]) -> bool:
    haystack = _normalize_path(path)
    return bool(
        haystack and any(needle in haystack for needle in _path_needles(case_context))
    )


def _process_name_matches(process_name: str, case_context: Mapping[str, Any]) -> bool:
    haystack = _normalize_path(process_name)
    needles = [
        _normalize_path(str(case_context.get("stored_filename") or "")),
        _normalize_path(str(case_context.get("original_filename") or "")),
        _normalize_path(str(case_context.get("case_id") or "")),
    ]
    return bool(haystack and any(needle and needle in haystack for needle in needles))


def _path_needles(case_context: Mapping[str, Any]) -> list[str]:
    return [
        needle
        for needle in (
            _normalize_path(str(case_context.get("sample_dir") or "")),
            _normalize_path(str(case_context.get("case_id") or "")),
            _normalize_path(str(case_context.get("stored_filename") or "")),
            _normalize_path(str(case_context.get("original_filename") or "")),
        )
        if needle
    ]


def _pid_matches(process_id: str, case_context: Mapping[str, Any]) -> bool:
    if not process_id:
        return False
    pids = {
        str(pid)
        for pid in [case_context.get("root_pid"), *case_context.get("child_pids", [])]
        if pid is not None
    }
    return process_id in pids


def _process_id(raw_event_data: object) -> str:
    if not isinstance(raw_event_data, Mapping):
        return ""
    by_casefold = {str(key).casefold(): key for key in raw_event_data}
    for name in ("Process ID", "ProcessId", "PID", "Process PID"):
        key = by_casefold.get(name.casefold())
        if key is not None:
            return str(raw_event_data.get(key) or "").strip()
    return ""


def _normalize_path(value: str) -> str:
    normalized = value.replace("/", "\\").casefold()
    for prefix in ("file:_", "file:", "\\\\?\\"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    return normalized


def _timestamp_in_window(timestamp_utc: str, window: CollectionWindow) -> bool:
    timestamp = _parse_utc(timestamp_utc)
    start = _parse_utc(window.start_utc)
    end = _parse_utc(window.end_utc)
    if timestamp is None or start is None or end is None:
        return False
    return start <= timestamp <= end


def _parse_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _windows_defender_artifacts() -> tuple[CollectorArtifact, ...]:
    return (
        CollectorArtifact(
            path="collector/normalized_evidence.json",
            category="normalized_evidence",
            include_in_evidence=True,
            redaction_owner="exporter",
            redaction_state="redacted",
            sensitivity="low",
            reason="derived normalized Windows Defender evidence with exporter text redaction",
        ),
    )
