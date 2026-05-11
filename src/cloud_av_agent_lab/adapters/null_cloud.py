from __future__ import annotations

from cloud_av_agent_lab.adapters.cloud import VMOperationResponse
from cloud_av_agent_lab.core.contracts import TestCase, VmProfile


class PlannedCloudVmAdapter:
    """Plan-only cloud adapter used for dry runs."""

    supports_execution = False

    def restore_snapshot(self, vm: VmProfile) -> VMOperationResponse:
        return self._response(
            action="restore_snapshot",
            message=f"plan: restore {vm.id} to snapshot {vm.baseline_snapshot}",
        )

    def start_vm(self, vm: VmProfile) -> VMOperationResponse:
        return self._response(
            action="start_vm",
            message=f"plan: start {vm.id} in network {vm.network_profile}",
        )

    def stop_vm(self, vm: VmProfile) -> VMOperationResponse:
        return self._response(action="stop_vm", message=f"plan: stop {vm.id}")

    def reboot_vm(self, vm: VmProfile) -> VMOperationResponse:
        return self._response(action="reboot_vm", message=f"plan: reboot {vm.id}")

    def get_instance_status(self, vm: VmProfile) -> VMOperationResponse:
        return self._response(
            action="get_instance_status",
            message=f"plan: query status for {vm.id}",
        )

    def resolve_instance_id(self, vm: VmProfile) -> str:
        return vm.instance_id or vm.cloud_instance_id or vm.id

    def capture_screenshot(self, case: TestCase) -> str:
        return f"plan://screenshots/{case.id}.png"

    def _response(self, action: str, message: str) -> VMOperationResponse:
        return VMOperationResponse(
            status="planned",
            task_id="",
            message=message,
            action=action,
            dry_run=True,
            provider="planned",
        )
