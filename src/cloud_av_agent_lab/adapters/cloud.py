from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from typing import Protocol

from cloud_av_agent_lab.core.contracts import TestCase, VmProfile


class CloudProviderError(RuntimeError):
    """Base error for cloud provider adapter failures."""


@dataclass(frozen=True)
class VMOperationResponse:
    status: str
    task_id: str
    message: str
    action: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    provider: str = ""

    def __str__(self) -> str:
        return self.message


class CloudVmAdapter(Protocol):
    supports_execution: bool

    def restore_snapshot(self, vm: VmProfile) -> VMOperationResponse:
        """Restore a VM to its clean baseline snapshot."""

    def start_vm(self, vm: VmProfile) -> VMOperationResponse:
        """Start a VM after snapshot restore."""

    def stop_vm(self, vm: VmProfile) -> VMOperationResponse:
        """Stop a VM after the case completes."""

    def reboot_vm(self, vm: VmProfile) -> VMOperationResponse:
        """Reboot a VM."""

    def get_instance_status(self, vm: VmProfile) -> VMOperationResponse:
        """Query VM instance status."""

    def capture_screenshot(self, case: TestCase) -> str:
        """Capture a cloud VM screenshot and return an artifact URI."""
