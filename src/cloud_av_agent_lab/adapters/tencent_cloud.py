from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from cloud_av_agent_lab.adapters.cloud import (
    CloudProviderError,
    VMOperationResponse,
)
from cloud_av_agent_lab.core.contracts import CloudProfile, TestCase, VmProfile
from cloud_av_agent_lab.network.client import NetworkClient


class TencentCloudApiNotConfigured(CloudProviderError):
    """Raised if real Tencent Cloud execution is requested before API wiring."""


class TencentCloudConfigError(CloudProviderError):
    """Raised when Tencent Cloud adapter configuration is invalid."""


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


class TencentCloudLighthouseAdapter:
    """Tencent Cloud Lighthouse adapter skeleton.

    The current implementation is intentionally plan-only. The real Tencent
    Cloud API integration should be added in `_call_api`, including request
    signing, credentials lookup, and SDK/client selection.
    """

    def __init__(
        self,
        cloud: CloudProfile,
        network: NetworkClient | None = None,
        dry_run: bool | None = None,
        env: Mapping[str, str] | None = None,
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
        self.supports_execution = self.mode == "real" and not self.dry_run

    def restore_snapshot(self, vm: VmProfile) -> VMOperationResponse:
        return self._operation(
            "ApplyInstanceSnapshot",
            vm,
            {
                "InstanceId": self._instance_id(vm),
                "SnapshotId": vm.baseline_snapshot,
            },
        )

    def start_vm(self, vm: VmProfile) -> VMOperationResponse:
        return self._operation(
            "StartInstances",
            vm,
            {"InstanceIds": [self._instance_id(vm)]},
        )

    def stop_vm(self, vm: VmProfile) -> VMOperationResponse:
        return self._operation(
            "StopInstances",
            vm,
            {"InstanceIds": [self._instance_id(vm)]},
        )

    def reboot_vm(self, vm: VmProfile) -> VMOperationResponse:
        return self._operation(
            "RebootInstances",
            vm,
            {"InstanceIds": [self._instance_id(vm)]},
        )

    def get_instance_status(self, vm: VmProfile) -> VMOperationResponse:
        return self._operation(
            "DescribeInstances",
            vm,
            {"InstanceIds": [self._instance_id(vm)]},
        )

    def capture_screenshot(self, case: TestCase) -> str:
        return f"tencent-cloud://screenshots/{case.vm.id}/{case.id}.png"

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

    def _call_api(self, operation: TencentCloudOperation) -> VMOperationResponse:
        raise TencentCloudApiNotConfigured(
            "Tencent Cloud API signing is not wired yet. Add credentials lookup, "
            "TC3-HMAC-SHA256 signing, and calls through NetworkClient here."
        )

    def _response(
        self,
        status: str,
        action: str,
        params: Mapping[str, object],
        message: str,
        dry_run: bool,
        task_id: str = "",
    ) -> VMOperationResponse:
        return VMOperationResponse(
            status=status,
            task_id=task_id,
            message=message,
            action=action,
            params=dict(params),
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
