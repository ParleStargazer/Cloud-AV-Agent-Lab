from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence

from .event_ids import event_kind_for_id
from .schema import WindowsDefenderParsedEvent


def parse_windows_defender_event_xml(xml_text: str) -> WindowsDefenderParsedEvent:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return _empty_event(raw_event_data={})

    event_id = _parse_int(_system_text(root, "EventID"))
    raw_items = _event_data_items(root)
    raw_event_data = _event_data_mapping(raw_items)
    return WindowsDefenderParsedEvent(
        event_id=event_id,
        event_kind=event_kind_for_id(event_id),
        observed_at_utc=_system_time_created(root),
        provider=_provider_name(root),
        channel=_system_text(root, "Channel"),
        computer=_system_text(root, "Computer"),
        record_id=_system_text(root, "EventRecordID"),
        threat_name=_first_field(
            raw_event_data,
            "Threat Name",
            "ThreatName",
            "Threat",
            "Name",
        ),
        threat_id=_first_field(raw_event_data, "Threat ID", "ThreatID"),
        severity=_first_field(
            raw_event_data, "Severity Name", "Severity", "Severity ID"
        ),
        category=_first_field(
            raw_event_data, "Category Name", "Category", "Category ID"
        ),
        path=_first_field(raw_event_data, "Path", "Detection Path", "Resource Path"),
        process_name=_first_field(
            raw_event_data,
            "Process Name",
            "ProcessName",
            "Process",
        ),
        user=_first_field(raw_event_data, "Detection User", "User", "Security User"),
        action=_first_field(raw_event_data, "Action Name", "Action", "Action ID"),
        action_status=_first_field(
            raw_event_data,
            "Status Code",
            "Status",
            "Action Status",
            "State",
        ),
        error_code=_first_field(raw_event_data, "Error Code", "ErrorCode"),
        error_description=_first_field(
            raw_event_data,
            "Error Description",
            "ErrorDescription",
        ),
        raw_event_data=raw_event_data,
        raw_event_data_items=tuple(raw_items),
    )


def _empty_event(raw_event_data: Mapping[str, str]) -> WindowsDefenderParsedEvent:
    return WindowsDefenderParsedEvent(
        event_id=None,
        event_kind="unknown",
        observed_at_utc=None,
        provider=None,
        channel=None,
        computer=None,
        record_id=None,
        threat_name=None,
        threat_id=None,
        severity=None,
        category=None,
        path=None,
        process_name=None,
        user=None,
        action=None,
        action_status=None,
        error_code=None,
        error_description=None,
        raw_event_data=raw_event_data,
        raw_event_data_items=(),
    )


def _system_text(root: ET.Element, name: str) -> str | None:
    element = _find_child(_find_child(root, "System"), name)
    return _clean_text(element.text if element is not None else None)


def _provider_name(root: ET.Element) -> str | None:
    provider = _find_child(_find_child(root, "System"), "Provider")
    if provider is None:
        return None
    return _clean_text(provider.attrib.get("Name"))


def _system_time_created(root: ET.Element) -> str | None:
    time_created = _find_child(_find_child(root, "System"), "TimeCreated")
    if time_created is None:
        return None
    return _clean_text(time_created.attrib.get("SystemTime"))


def _event_data_items(root: ET.Element) -> list[tuple[str, str]]:
    event_data = _find_child(root, "EventData")
    if event_data is None:
        return []
    items: list[tuple[str, str]] = []
    unnamed_index = 0
    for child in list(event_data):
        if _local_name(child.tag) != "Data":
            continue
        name = _clean_text(child.attrib.get("Name"))
        if not name:
            unnamed_index += 1
            name = f"Data{unnamed_index}"
        value = _clean_text(child.text) or ""
        items.append((name, value))
    return items


def _event_data_mapping(items: Sequence[tuple[str, str]]) -> dict[str, str]:
    payload: dict[str, str] = {}
    counts: dict[str, int] = {}
    for name, value in items:
        if name not in payload:
            payload[name] = value
            counts[name] = 1
            continue
        counts[name] = counts.get(name, 1) + 1
        payload[f"{name}#{counts[name]}"] = value
    return payload


def _first_field(payload: Mapping[str, str], *names: str) -> str | None:
    by_casefold = {key.casefold(): key for key in payload}
    for name in names:
        key = by_casefold.get(name.casefold())
        if key is not None:
            return _clean_text(payload.get(key))
    return None


def _find_child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in list(parent):
        if _local_name(child.tag) == name:
            return child
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
