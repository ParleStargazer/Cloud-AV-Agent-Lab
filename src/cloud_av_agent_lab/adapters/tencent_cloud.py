from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from cloud_av_agent_lab.adapters.cloud import (
    CloudProviderError,
    VMOperationResponse,
)
from cloud_av_agent_lab.core.contracts import CloudProfile, TestCase, VmProfile
from cloud_av_agent_lab.network.client import NetworkClient, encode_json_payload

logger = logging.getLogger(__name__)


class TencentCloudConfigError(CloudProviderError):
    """Raised when Tencent Cloud adapter configuration is invalid."""


class TencentCloudApiError(CloudProviderError):
    """Raised when Tencent Cloud returns an API error response."""

    def __init__(self, code: str, message: str, request_id: str = "") -> None:
        self.code = code
        self.request_id = request_id
        detail = f"{code}: {message}"
        if request_id:
            detail = f"{detail} (RequestId: {request_id})"
        super().__init__(detail)


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


def resolve_tencent_cloud_auth(
    cloud: CloudProfile,
    env: Mapping[str, str] | None = None,
) -> TencentCloudAuth:
    values = env if env is not None else os.environ
    secret_id, secret_id_source = _env_or_config(
        values,
        "TENCENTCLOUD_SECRET_ID",
        cloud.secret_id,
    )
    secret_key, secret_key_source = _env_or_config(
        values,
        "TENCENTCLOUD_SECRET_KEY",
        cloud.secret_key,
    )
    region, region_source = _env_or_config(
        values,
        "TENCENTCLOUD_REGION",
        cloud.region,
    )
    return TencentCloudAuth(
        secret_id=secret_id,
        secret_key=secret_key,
        region=region,
        secret_id_source=secret_id_source,
        secret_key_source=secret_key_source,
        region_source=region_source,
    )


def _env_or_config(
    env: Mapping[str, str],
    key: str,
    config_value: str,
) -> tuple[str, str]:
    env_value = env.get(key, "")
    if env_value:
        return env_value, "env"
    return config_value, "config"


def _env_suffix(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


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


def parse_lighthouse_instance_status(
    response_payload: Mapping[str, object],
    expected_instance_id: str = "",
) -> LighthouseInstanceStatus:
    instance_set = response_payload.get("InstanceSet")
    if not isinstance(instance_set, list):
        raise CloudProviderError(
            "Tencent Cloud DescribeInstances response missing InstanceSet list"
        )

    instances = [item for item in instance_set if isinstance(item, Mapping)]
    if expected_instance_id:
        instances = [
            item
            for item in instances
            if _as_str(item.get("InstanceId")) == expected_instance_id
        ]

    if not instances:
        suffix = f" for {expected_instance_id}" if expected_instance_id else ""
        raise CloudProviderError(
            f"Tencent Cloud DescribeInstances returned no Lighthouse instance{suffix}"
        )
    if len(instances) > 1:
        raise CloudProviderError(
            "Tencent Cloud DescribeInstances returned multiple Lighthouse instances; "
            "query a single expected instance before running validations"
        )

    instance = instances[0]
    internet = _as_mapping(instance.get("InternetAccessible"))
    return LighthouseInstanceStatus(
        instance_id=_as_str(instance.get("InstanceId")),
        name=_as_str(instance.get("InstanceName")),
        state=_as_str(instance.get("InstanceState")),
        restrict_state=_as_str(instance.get("InstanceRestrictState")),
        latest_operation=_as_str(instance.get("LatestOperation")),
        latest_operation_state=_as_str(instance.get("LatestOperationState")),
        latest_operation_request_id=_as_str(instance.get("LatestOperationRequestId")),
        zone=_as_str(instance.get("Zone")),
        platform=_as_str(instance.get("Platform")),
        os_name=_as_str(instance.get("OsName")),
        private_ipv4=_as_string_tuple(instance.get("PrivateAddresses")),
        public_ipv4=_as_string_tuple(instance.get("PublicAddresses")),
        public_ipv4_assigned=_as_bool(internet.get("PublicIpAssigned")),
        created_time=_as_str(instance.get("CreatedTime")),
        expired_time=_as_str(instance.get("ExpiredTime")),
        request_id=_as_str(response_payload.get("RequestId")),
        total_count=_as_int(response_payload.get("TotalCount")),
    )


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return False


def _as_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return int(_as_str(value))
    except ValueError:
        return 0


def _as_string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_as_str(item) for item in value if item is not None)


def build_tc3_headers(
    secret_id: str,
    secret_key: str,
    endpoint: str,
    action: str,
    version: str,
    region: str,
    payload: Mapping[str, object],
    timestamp: int,
) -> dict[str, str]:
    host = _endpoint_host(endpoint)
    service = host.split(".", maxsplit=1)[0]
    algorithm = "TC3-HMAC-SHA256"
    content_type = "application/json; charset=utf-8"
    signed_headers = "content-type;host"
    canonical_headers = f"content-type:{content_type}\nhost:{host}\n"
    hashed_payload = hashlib.sha256(encode_json_payload(payload)).hexdigest()
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            hashed_payload,
        ]
    )
    request_date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
    credential_scope = f"{request_date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        [
            algorithm,
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = _sign_tc3(secret_key, request_date, service, string_to_sign)
    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    return {
        "Authorization": authorization,
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": region,
    }


def _sign_tc3(
    secret_key: str,
    request_date: str,
    service: str,
    string_to_sign: str,
) -> str:
    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), request_date)
    secret_service = _hmac_sha256(secret_date, service)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    return hmac.new(
        secret_signing,
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _endpoint_host(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if not parsed.netloc:
        raise TencentCloudConfigError(f"invalid Tencent Cloud endpoint: {endpoint}")
    return parsed.netloc


def _decode_json_response(body: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudProviderError("Tencent Cloud response is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise CloudProviderError("Tencent Cloud response JSON must be an object")
    return decoded
