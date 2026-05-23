from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

PRODUCT_ID = "windows-defender"
PRODUCT_LOG_SOURCE = "product_log"
OPERATIONAL_CHANNEL = "Microsoft-Windows-Windows Defender/Operational"
PROVIDER_NAME = "Microsoft-Windows-Windows Defender"


@dataclass(frozen=True)
class WindowsDefenderParsedEvent:
    event_id: int | None
    event_kind: str
    observed_at_utc: str | None
    provider: str | None
    channel: str | None
    computer: str | None
    record_id: str | None
    threat_name: str | None
    threat_id: str | None
    severity: str | None
    category: str | None
    path: str | None
    process_name: str | None
    user: str | None
    action: str | None
    action_status: str | None
    error_code: str | None
    error_description: str | None
    raw_event_data: Mapping[str, str] = field(default_factory=dict)
    raw_event_data_items: Sequence[tuple[str, str]] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_kind": self.event_kind,
            "observed_at_utc": self.observed_at_utc,
            "provider": self.provider,
            "channel": self.channel,
            "computer": self.computer,
            "record_id": self.record_id,
            "threat_name": self.threat_name,
            "threat_id": self.threat_id,
            "severity": self.severity,
            "category": self.category,
            "path": self.path,
            "process_name": self.process_name,
            "user": self.user,
            "action": self.action,
            "action_status": self.action_status,
            "error_code": self.error_code,
            "error_description": self.error_description,
            "raw_event_data": dict(self.raw_event_data),
            "raw_event_data_items": [
                {"name": name, "value": value}
                for name, value in self.raw_event_data_items
            ],
        }
