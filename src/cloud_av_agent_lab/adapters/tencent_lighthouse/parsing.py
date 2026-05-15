from __future__ import annotations

import json
from collections.abc import Mapping

from cloud_av_agent_lab.adapters.cloud import CloudProviderError

from .models import LighthouseInstanceStatus


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


def _decode_json_response(body: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudProviderError("Tencent Cloud response is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise CloudProviderError("Tencent Cloud response JSON must be an object")
    return decoded
