from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TencentCloudAuth:
    secret_id: str
    secret_key: str
    region: str
    secret_id_source: str
    secret_key_source: str
    region_source: str


@dataclass(frozen=True)
class TencentCloudOperation:
    action: str
    endpoint: str
    region: str
    instance_id: str
    params: dict[str, object]
    dry_run: bool
    mode: str


KNOWN_LIGHTHOUSE_INSTANCE_STATES = frozenset(
    {
        "PENDING",
        "LAUNCH_FAILED",
        "RUNNING",
        "STOPPED",
        "STARTING",
        "STOPPING",
        "REBOOTING",
        "SHUTDOWN",
        "TERMINATING",
    }
)
STABLE_LIGHTHOUSE_OPERATION_STATES = frozenset({"", "SUCCESS"})
DEFAULT_POLL_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class LighthouseInstanceStatus:
    instance_id: str
    name: str
    state: str
    restrict_state: str
    latest_operation: str = ""
    latest_operation_state: str = ""
    latest_operation_request_id: str = ""
    zone: str = ""
    platform: str = ""
    os_name: str = ""
    private_ipv4: tuple[str, ...] = ()
    public_ipv4: tuple[str, ...] = ()
    public_ipv4_assigned: bool = False
    created_time: str = ""
    expired_time: str = ""
    request_id: str = ""
    total_count: int = 0

    @property
    def known_state(self) -> bool:
        return self.state in KNOWN_LIGHTHOUSE_INSTANCE_STATES

    @property
    def control_plane_ready(self) -> bool:
        return (
            self.restrict_state in {"", "NORMAL"}
            and self.latest_operation_state in STABLE_LIGHTHOUSE_OPERATION_STATES
        )

    @property
    def guest_access_ready(self) -> bool:
        return self.state == "RUNNING" and self.known_state and self.control_plane_ready

    @property
    def can_start(self) -> bool:
        return self.state == "STOPPED" and self.known_state and self.control_plane_ready

    @property
    def can_stop(self) -> bool:
        return self.state == "RUNNING" and self.known_state and self.control_plane_ready

    @property
    def can_reboot(self) -> bool:
        return self.state == "RUNNING" and self.known_state and self.control_plane_ready

    @property
    def can_restore_snapshot(self) -> bool:
        return self.state == "STOPPED" and self.known_state and self.control_plane_ready

    @property
    def blocked_reason(self) -> str:
        if not self.known_state:
            return f"unknown Lighthouse instance state: {self.state or '<empty>'}"
        if self.restrict_state not in {"", "NORMAL"}:
            return f"instance restrict state is {self.restrict_state}"
        if self.latest_operation_state not in STABLE_LIGHTHOUSE_OPERATION_STATES:
            return (
                "latest operation is not stable: "
                f"{self.latest_operation or '<unknown>'}="
                f"{self.latest_operation_state or '<empty>'}"
            )
        if self.state != "RUNNING":
            return f"instance state is {self.state}"
        return ""

    def operation_allowed(self) -> dict[str, bool]:
        return {
            "guest_access": self.guest_access_ready,
            "start": self.can_start,
            "stop": self.can_stop,
            "reboot": self.can_reboot,
            "restore_snapshot": self.can_restore_snapshot,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "state": self.state,
            "restrict_state": self.restrict_state,
            "known_state": self.known_state,
            "control_plane_ready": self.control_plane_ready,
            "guest_access_ready": self.guest_access_ready,
            "blocked_reason": self.blocked_reason,
            "operation_allowed": self.operation_allowed(),
            "latest_operation": self.latest_operation,
            "latest_operation_state": self.latest_operation_state,
            "latest_operation_request_id": self.latest_operation_request_id,
            "zone": self.zone,
            "platform": self.platform,
            "os_name": self.os_name,
            "private_ipv4": list(self.private_ipv4),
            "public_ipv4": list(self.public_ipv4),
            "public_ipv4_assigned": self.public_ipv4_assigned,
            "created_time": self.created_time,
            "expired_time": self.expired_time,
            "request_id": self.request_id,
            "total_count": self.total_count,
        }
