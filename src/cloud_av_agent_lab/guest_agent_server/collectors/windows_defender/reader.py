from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import platform
import xml.etree.ElementTree as ET
from typing import Protocol


class WindowsEventLogError(RuntimeError):
    """Base error for Windows Event Log reader failures."""


class WindowsEventLogChannelNotFound(WindowsEventLogError):
    """Raised when the target event log channel is not available."""


class WindowsEventLogAccessDenied(WindowsEventLogError):
    """Raised when the reader cannot access the target event log channel."""


class WindowsEventLogQueryFailed(WindowsEventLogError):
    """Raised when an event log query fails after the channel is available."""


@dataclass(frozen=True)
class WindowsEventRecord:
    event_id: int | None
    xml: str
    observed_at_utc: str | None = None
    record_id: str | None = None


class WindowsEventLogReader(Protocol):
    def query(
        self,
        channel: str,
        event_ids: Sequence[int],
        start_time_utc: datetime | None = None,
        end_time_utc: datetime | None = None,
        limit: int = 50,
    ) -> Sequence[WindowsEventRecord]:
        """Return structured records from a Windows Event Log channel."""


class PyWin32WindowsEventLogReader:
    def query(
        self,
        channel: str,
        event_ids: Sequence[int],
        start_time_utc: datetime | None = None,
        end_time_utc: datetime | None = None,
        limit: int = 50,
    ) -> Sequence[WindowsEventRecord]:
        if platform.system() != "Windows":
            raise WindowsEventLogQueryFailed(
                "Windows Event Log reader is only available on Windows"
            )
        try:
            import pywintypes  # type: ignore[import-not-found]
            import win32evtlog  # type: ignore[import-not-found]
        except ImportError as exc:
            raise WindowsEventLogQueryFailed(
                "pywin32 is required for the Windows Event Log reader"
            ) from exc

        query = _build_xpath_query(event_ids, start_time_utc, end_time_utc)
        handle = None
        records: list[WindowsEventRecord] = []
        try:
            handle = win32evtlog.EvtQuery(
                channel,
                win32evtlog.EvtQueryChannelPath,
                query,
            )
            while len(records) < limit:
                batch_size = min(10, limit - len(records))
                event_handles = win32evtlog.EvtNext(handle, batch_size, 1000, 0)
                if not event_handles:
                    break
                for event_handle in event_handles:
                    try:
                        xml = win32evtlog.EvtRender(
                            event_handle,
                            win32evtlog.EvtRenderEventXml,
                        )
                        event_id, observed_at_utc, record_id = _parse_event_metadata(
                            xml
                        )
                        records.append(
                            WindowsEventRecord(
                                event_id=event_id,
                                xml=xml,
                                observed_at_utc=observed_at_utc,
                                record_id=record_id,
                            )
                        )
                    finally:
                        _close_event_handle(win32evtlog, event_handle)
        except pywintypes.error as exc:
            raise _map_pywin32_error(exc, channel) from exc
        finally:
            if handle is not None:
                _close_event_handle(win32evtlog, handle)
        return tuple(records)


def _build_xpath_query(
    event_ids: Sequence[int],
    start_time_utc: datetime | None,
    end_time_utc: datetime | None,
) -> str:
    clauses = [f"EventID={int(event_id)}" for event_id in event_ids]
    event_id_filter = " or ".join(clauses) or "EventID >= 0"
    time_filters: list[str] = []
    if start_time_utc is not None:
        time_filters.append(
            f"@SystemTime >= '{_format_win_event_time(start_time_utc)}'"
        )
    if end_time_utc is not None:
        time_filters.append(f"@SystemTime <= '{_format_win_event_time(end_time_utc)}'")
    if time_filters:
        return (
            "*[System[("
            + event_id_filter
            + ") and TimeCreated["
            + " and ".join(time_filters)
            + "]]]"
        )
    return "*[System[(" + event_id_filter + ")]]"


def _format_win_event_time(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_event_metadata(xml: str) -> tuple[int | None, str | None, str | None]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None, None, None
    system = _find_child(root, "System")
    event_id = _parse_int(_child_text(system, "EventID"))
    record_id = _child_text(system, "EventRecordID")
    time_created = _find_child(system, "TimeCreated")
    observed_at_utc = (
        _clean_text(time_created.attrib.get("SystemTime"))
        if time_created is not None
        else None
    )
    return event_id, observed_at_utc, record_id


def _find_child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in list(parent):
        if child.tag.rsplit("}", maxsplit=1)[-1] == name:
            return child
    return None


def _child_text(parent: ET.Element | None, name: str) -> str | None:
    child = _find_child(parent, name)
    return _clean_text(child.text if child is not None else None)


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


def _map_pywin32_error(exc: object, channel: str) -> WindowsEventLogError:
    winerror = int(getattr(exc, "winerror", 0) or 0)
    message = str(exc).casefold()
    if winerror == 5 or ("access" in message and "denied" in message):
        return WindowsEventLogAccessDenied(
            f"access denied while querying event log channel {channel!r}"
        )
    if "not found" in message or "cannot find" in message:
        return WindowsEventLogChannelNotFound(
            f"event log channel was not found: {channel}"
        )
    return WindowsEventLogQueryFailed(
        f"failed to query event log channel {channel!r}: {type(exc).__name__}"
    )


def _close_event_handle(win32evtlog: object, handle: object) -> None:
    try:
        close = getattr(win32evtlog, "EvtClose")
    except AttributeError:
        return
    try:
        close(handle)
    except Exception:
        return
