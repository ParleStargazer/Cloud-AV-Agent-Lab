from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import urlparse

from cloud_av_agent_lab.adapters.cloud import (
    CloudProviderError,
    VMOperationResponse,
)
from cloud_av_agent_lab.core.contracts import CloudProfile, TestCase, VmProfile
from cloud_av_agent_lab.network.client import NetworkClient, encode_json_payload


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

        return self._response(
            status="success",
            action=operation.action,
            params=operation.params,
            data=response_payload,
            message=(
                f"tencent-cloud lighthouse: {operation.action} accepted"
                + (f" (RequestId: {request_id})" if request_id else "")
            ),
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
