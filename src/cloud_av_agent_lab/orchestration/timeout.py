from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NetworkTimeoutProfile:
    connect_seconds: float
    read_seconds: float

    def socket_timeout_seconds(self) -> float:
        """Return the current urllib-compatible timeout value.

        NetworkClient currently exposes one socket timeout. Keeping a connect/read
        profile at the orchestration boundary lets us swap the transport later
        without changing single-run control flow.
        """

        return max(float(self.connect_seconds), float(self.read_seconds))


GUEST_HEALTH_TIMEOUT = NetworkTimeoutProfile(connect_seconds=3.0, read_seconds=5.0)
GUEST_CONTROL_TIMEOUT = NetworkTimeoutProfile(connect_seconds=5.0, read_seconds=30.0)
EVIDENCE_EXPORT_TIMEOUT = NetworkTimeoutProfile(connect_seconds=5.0, read_seconds=120.0)
SALVAGE_TIMEOUT = NetworkTimeoutProfile(connect_seconds=2.0, read_seconds=5.0)
