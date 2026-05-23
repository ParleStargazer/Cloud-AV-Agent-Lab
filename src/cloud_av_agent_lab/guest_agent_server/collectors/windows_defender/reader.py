from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
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
