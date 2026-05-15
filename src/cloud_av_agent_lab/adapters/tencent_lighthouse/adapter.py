from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import replace

from cloud_av_agent_lab.adapters.cloud import (
    CloudProviderError,
    VMOperationResponse,
)
from cloud_av_agent_lab.core.contracts import CloudProfile, TestCase, VmProfile
from cloud_av_agent_lab.network.client import NetworkClient

from .auth import _env_suffix, resolve_tencent_cloud_auth
from .errors import TencentCloudApiError, TencentCloudConfigError
from .models import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_POLL_TIMEOUT_SECONDS,
    LighthouseInstanceStatus,
    TencentCloudOperation,
)
from .parsing import _decode_json_response, parse_lighthouse_instance_status
from .signing import build_tc3_headers

logger = logging.getLogger("cloud_av_agent_lab.adapters.tencent_cloud")


class TencentCloudLighthouseAdapter:
    """Tencent Cloud Lighthouse adapter skeleton.

    Real API requests are protected by `dry_run` by default. When dry-run is
    disabled, `_call_api` signs TC3-HMAC-SHA256 requests and sends them through
    the shared NetworkClient.
    """

    def __init__(
        self,
        cloud: CloudProfile,
        network: NetworkClient | None = None,
        dry_run: bool | None = None,
        env: Mapping[str, str] | None = None,
        confirmed_instance_id: str = "",
        confirmed_snapshot_id: str = "",
        poll_timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.cloud = cloud
        self.network = network or NetworkClient()
        self.env = env if env is not None else os.environ
        self.mode = cloud.mode.casefold()
        if self.mode not in {"mock", "real"}:
            raise TencentCloudConfigError(
                "TencentCloudLighthouseAdapter mode must be mock or real"
            )
        self.dry_run = cloud.dry_run if dry_run is None else dry_run
        self.auth = resolve_tencent_cloud_auth(cloud, self.env)
        self.confirmed_instance_id = confirmed_instance_id.strip()
        self.confirmed_snapshot_id = confirmed_snapshot_id.strip()
        self.supports_execution = (
            self.mode == "real"
            and not self.dry_run
            and bool(self.confirmed_instance_id)
        )
        self.poll_timeout_seconds = poll_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def restore_snapshot(self, vm: VmProfile) -> VMOperationResponse:
        return self._restore_snapshot_operation(
            vm,
            "ApplyInstanceSnapshot",
            {
                "InstanceId": self._instance_id(vm),
                "SnapshotId": vm.baseline_snapshot,
            },
        )

    def start_vm(self, vm: VmProfile) -> VMOperationResponse:
        return self._write_operation(
            "StartInstances",
            vm,
            {"InstanceIds": [self._instance_id(vm)]},
            target_state="RUNNING",
        )

    def stop_vm(self, vm: VmProfile) -> VMOperationResponse:
        return self._write_operation(
            "StopInstances",
            vm,
            {"InstanceIds": [self._instance_id(vm)]},
            target_state="STOPPED",
        )

    def reboot_vm(self, vm: VmProfile) -> VMOperationResponse:
        return self._write_operation(
            "RebootInstances",
            vm,
            {"InstanceIds": [self._instance_id(vm)]},
            target_state="RUNNING",
        )

    def get_instance_status(self, vm: VmProfile) -> VMOperationResponse:
        return self._operation(
            "DescribeInstances",
            vm,
            {"InstanceIds": [self._instance_id(vm)]},
        )

    def capture_screenshot(self, case: TestCase) -> str:
        return f"tencent-cloud://screenshots/{case.vm.id}/{case.id}.png"

    def resolve_instance_id(self, vm: VmProfile) -> str:
        return self._instance_id(vm)

    def wait_instance_status(
        self,
        vm: VmProfile,
        target_state: str,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
        expected_latest_operation: str = "",
        expected_operation_request_id: str = "",
    ) -> LighthouseInstanceStatus:
        return self.wait_instance_statuses(
            vm,
            (target_state,),
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            expected_latest_operation=expected_latest_operation,
            expected_operation_request_id=expected_operation_request_id,
        )

    def wait_instance_statuses(
        self,
        vm: VmProfile,
        target_states: tuple[str, ...],
        timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
        expected_latest_operation: str = "",
        expected_operation_request_id: str = "",
    ) -> LighthouseInstanceStatus:
        timeout = (
            self.poll_timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        poll_interval = (
            self.poll_interval_seconds
            if poll_interval_seconds is None
            else float(poll_interval_seconds)
        )
        expected_instance_id = self._instance_id(vm)
        normalized_targets = tuple(state.upper() for state in target_states)
        started_at = time.monotonic()
        deadline = started_at + timeout

        while True:
            response = self.get_instance_status(vm)
            current_status = parse_lighthouse_instance_status(
                response.data,
                expected_instance_id=expected_instance_id,
            )
            elapsed_seconds = time.monotonic() - started_at
            logger.info(
                "Polling instance %s: state=%s, latest_operation=%s, "
                "latest_operation_state=%s, waited=%.1fs",
                current_status.instance_id,
                current_status.state,
                current_status.latest_operation or "<none>",
                current_status.latest_operation_state or "<none>",
                elapsed_seconds,
            )

            if current_status.latest_operation_state == "FAILED":
                raise CloudProviderError(
                    "Tencent Cloud Lighthouse operation failed while waiting for "
                    f"{expected_instance_id}: "
                    f"{current_status.latest_operation or '<unknown>'}"
                )

            if (
                current_status.state in normalized_targets
                and current_status.control_plane_ready
                and _matches_expected_operation(
                    current_status,
                    expected_latest_operation,
                    expected_operation_request_id,
                )
            ):
                return current_status

            now = time.monotonic()
            if now >= deadline:
                raise CloudProviderError(
                    "Timed out waiting for Tencent Cloud Lighthouse instance "
                    f"{expected_instance_id} to reach {'/'.join(normalized_targets)}; "
                    f"last state={current_status.state}, "
                    f"latest_operation_state={current_status.latest_operation_state}"
                )

            time.sleep(min(max(poll_interval, 0.0), max(deadline - now, 0.0)))

    def describe_operation(
        self,
        action: str,
        vm: VmProfile,
        params: Mapping[str, object] | None = None,
    ) -> TencentCloudOperation:
        return TencentCloudOperation(
            action=action,
            endpoint=self.cloud.api_endpoint,
            region=self.auth.region or vm.region or self.cloud.region,
            instance_id=self._instance_id(vm),
            params=dict(params or {}),
            dry_run=self.dry_run,
            mode=self.mode,
        )

    def _operation(
        self,
        action: str,
        vm: VmProfile,
        params: Mapping[str, object] | None = None,
    ) -> VMOperationResponse:
        operation = self.describe_operation(action, vm, params)
        if self.dry_run:
            return self._response(
                status="dry-run",
                action=action,
                params=operation.params,
                message=(
                    f"[DRY-RUN] Would call: {action} with Params: {operation.params}"
                ),
                dry_run=True,
            )

        if self.mode == "mock":
            proxy_note = " via proxy" if self.network.proxy_map else ""
            return self._response(
                status="mock",
                action=action,
                params=operation.params,
                message=(
                    f"tencent-cloud mock: {operation.action} "
                    f"{operation.instance_id} in {operation.region}{proxy_note}"
                ),
                dry_run=False,
            )

        return self._call_api(operation)

    def _write_operation(
        self,
        action: str,
        vm: VmProfile,
        params: Mapping[str, object],
        target_state: str,
    ) -> VMOperationResponse:
        instance_id = self._instance_id(vm)
        if (
            self.mode == "real"
            and not self.dry_run
            and self.confirmed_instance_id != instance_id
        ):
            return self._response(
                status="dry-run",
                action=action,
                params=params,
                message=(
                    f"[DRY-RUN] Would call: {action} with Params: {dict(params)} "
                    "(write confirmation missing or mismatched)"
                ),
                dry_run=True,
            )

        response = self._operation(action, vm, params)
        if response.status != "success" or self.dry_run or self.mode != "real":
            return response

        logger.info(
            "API Request Accepted, RequestId: %s",
            response.task_id or "<empty>",
        )
        final_status = self.wait_instance_status(
            vm,
            target_state,
            expected_latest_operation=action,
            expected_operation_request_id=response.task_id,
        )
        data = dict(response.data)
        data["FinalInstanceStatus"] = final_status.to_dict()
        return replace(
            response,
            message=(
                f"{response.message}; {final_status.instance_id} "
                f"reached {final_status.state}"
            ),
            data=data,
        )

    def _restore_snapshot_operation(
        self,
        vm: VmProfile,
        action: str,
        params: Mapping[str, object],
    ) -> VMOperationResponse:
        instance_id = self._instance_id(vm)
        snapshot_id = vm.baseline_snapshot
        if (
            self.mode == "real"
            and not self.dry_run
            and (
                self.confirmed_instance_id != instance_id
                or self.confirmed_snapshot_id != snapshot_id
            )
        ):
            return self._response(
                status="dry-run",
                action=action,
                params=params,
                message=(
                    f"[DRY-RUN] Would call: {action} with Params: {dict(params)} "
                    "(restore confirmation missing or mismatched)"
                ),
                dry_run=True,
            )

        if self.mode != "real" or self.dry_run:
            return self._operation(action, vm, params)

        precheck_status = self._precheck_snapshot_restore(vm)
        response = self._operation(action, vm, params)
        if response.status != "success":
            return response

        logger.info(
            "API Request Accepted, RequestId: %s",
            response.task_id or "<empty>",
        )
        post_restore_status = self._wait_snapshot_restore_settled(
            vm,
            action,
            response.task_id,
        )
        data = dict(response.data)
        data["PrecheckInstanceStatus"] = precheck_status.to_dict()
        data["PostRestoreInstanceStatus"] = post_restore_status.to_dict()

        if post_restore_status.state != "RUNNING":
            logger.info(
                "Snapshot restore completed with state=%s; starting instance %s",
                post_restore_status.state,
                post_restore_status.instance_id,
            )
            start_response = self.start_vm(vm)
            data["StartAfterRestore"] = start_response.data
            data["FinalInstanceStatus"] = start_response.data.get(
                "FinalInstanceStatus",
                {},
            )
        else:
            data["FinalInstanceStatus"] = post_restore_status.to_dict()

        final_status_data = data.get("FinalInstanceStatus")
        final_state = (
            final_status_data.get("state", "<unknown>")
            if isinstance(final_status_data, dict)
            else "<unknown>"
        )
        return replace(
            response,
            message=(
                f"{response.message}; {instance_id} snapshot restored "
                f"and reached {final_state}"
            ),
            data=data,
        )

    def _precheck_snapshot_restore(self, vm: VmProfile) -> LighthouseInstanceStatus:
        instance_id = self._instance_id(vm)
        response = self.get_instance_status(vm)
        status = parse_lighthouse_instance_status(
            response.data,
            expected_instance_id=instance_id,
        )
        if status.latest_operation_state == "FAILED":
            raise CloudProviderError(
                "Cannot restore snapshot because latest Lighthouse operation failed: "
                f"{status.latest_operation or '<unknown>'}"
            )
        if status.state == "RUNNING":
            raise CloudProviderError(
                f"Cannot restore snapshot for {instance_id}: instance is RUNNING; "
                "please stop the instance first"
            )
        if status.state != "STOPPED":
            raise CloudProviderError(
                f"Cannot restore snapshot for {instance_id}: instance state is "
                f"{status.state}; expected STOPPED"
            )
        if not status.control_plane_ready:
            raise CloudProviderError(
                f"Cannot restore snapshot for {instance_id}: instance has an "
                "in-progress or restricted control-plane state"
            )
        return status

    def _wait_snapshot_restore_settled(
        self,
        vm: VmProfile,
        action: str,
        request_id: str,
    ) -> LighthouseInstanceStatus:
        timeout = self.poll_timeout_seconds
        poll_interval = self.poll_interval_seconds
        expected_instance_id = self._instance_id(vm)
        started_at = time.monotonic()
        deadline = started_at + timeout

        while True:
            response = self.get_instance_status(vm)
            current_status = parse_lighthouse_instance_status(
                response.data,
                expected_instance_id=expected_instance_id,
            )
            elapsed_seconds = time.monotonic() - started_at
            logger.info(
                "Polling instance %s: state=%s, latest_operation=%s, "
                "latest_operation_state=%s, waited=%.1fs",
                current_status.instance_id,
                current_status.state,
                current_status.latest_operation or "<none>",
                current_status.latest_operation_state or "<none>",
                elapsed_seconds,
            )

            if current_status.latest_operation_state == "FAILED":
                raise CloudProviderError(
                    "Tencent Cloud Lighthouse snapshot restore failed while "
                    f"waiting for {expected_instance_id}: "
                    f"{current_status.latest_operation or '<unknown>'}"
                )

            restore_request_settled = (
                current_status.latest_operation == action
                and current_status.latest_operation_request_id == request_id
                and current_status.state in {"STOPPED", "RUNNING"}
                and current_status.control_plane_ready
            )
            already_running = (
                current_status.state == "RUNNING" and current_status.control_plane_ready
            )
            if restore_request_settled or already_running:
                return current_status

            now = time.monotonic()
            if now >= deadline:
                raise CloudProviderError(
                    "Timed out waiting for Tencent Cloud Lighthouse snapshot "
                    f"restore on {expected_instance_id}; "
                    f"last state={current_status.state}, "
                    f"latest_operation_state={current_status.latest_operation_state}"
                )

            time.sleep(min(max(poll_interval, 0.0), max(deadline - now, 0.0)))

    def _call_api(self, operation: TencentCloudOperation) -> VMOperationResponse:
        if not self.auth.secret_id or not self.auth.secret_key:
            raise TencentCloudConfigError(
                "TENCENTCLOUD_SECRET_ID and TENCENTCLOUD_SECRET_KEY are required "
                "for real Tencent Cloud API calls"
            )

        timestamp = int(time.time())
        headers = build_tc3_headers(
            secret_id=self.auth.secret_id,
            secret_key=self.auth.secret_key,
            endpoint=operation.endpoint,
            action=operation.action,
            version=self.cloud.api_version,
            region=operation.region,
            payload=operation.params,
            timestamp=timestamp,
        )
        try:
            response = self.network.request_json(
                method="POST",
                url=operation.endpoint,
                payload=operation.params,
                headers=headers,
            )
        except Exception as exc:
            raise CloudProviderError(
                f"Tencent Cloud {operation.action} request failed: {exc}"
            ) from exc

        body = _decode_json_response(response.body)
        response_payload = body.get("Response")
        if not isinstance(response_payload, dict):
            raise CloudProviderError(
                f"Tencent Cloud {operation.action} response missing Response object"
            )

        error = response_payload.get("Error")
        request_id = str(response_payload.get("RequestId", ""))
        if isinstance(error, dict):
            raise TencentCloudApiError(
                code=str(error.get("Code", "UnknownError")),
                message=str(error.get("Message", "")),
                request_id=request_id,
            )

        data: dict[str, object] = dict(response_payload)
        instance_status: LighthouseInstanceStatus | None = None
        if operation.action == "DescribeInstances":
            instance_status = parse_lighthouse_instance_status(
                response_payload,
                expected_instance_id=operation.instance_id,
            )
            data["InstanceStatus"] = instance_status.to_dict()

        message = f"tencent-cloud lighthouse: {operation.action} accepted" + (
            f" (RequestId: {request_id})" if request_id else ""
        )
        if instance_status is not None:
            message = (
                f"{message}; {instance_status.instance_id} "
                f"state={instance_status.state} "
                f"guest_access_ready={instance_status.guest_access_ready}"
            )

        return self._response(
            status="success",
            action=operation.action,
            params=operation.params,
            data=data,
            message=message,
            dry_run=False,
            task_id=request_id,
        )

    def _response(
        self,
        status: str,
        action: str,
        params: Mapping[str, object],
        message: str,
        dry_run: bool,
        task_id: str = "",
        data: Mapping[str, object] | None = None,
    ) -> VMOperationResponse:
        return VMOperationResponse(
            status=status,
            task_id=task_id,
            message=message,
            action=action,
            params=dict(params),
            data=dict(data or {}),
            dry_run=dry_run,
            provider="tencent-cloud-lighthouse",
        )

    def _instance_id(self, vm: VmProfile) -> str:
        env_key = f"TENCENTCLOUD_INSTANCE_ID_{_env_suffix(vm.id)}"
        return (
            self.env.get(env_key)
            or self.env.get("TENCENTCLOUD_INSTANCE_ID")
            or vm.instance_id
            or vm.cloud_instance_id
            or vm.id
        )


def _matches_expected_operation(
    status: LighthouseInstanceStatus,
    expected_latest_operation: str,
    expected_operation_request_id: str,
) -> bool:
    if (
        expected_latest_operation
        and status.latest_operation != expected_latest_operation
    ):
        return False
    if (
        expected_operation_request_id
        and status.latest_operation_request_id != expected_operation_request_id
    ):
        return False
    return True
