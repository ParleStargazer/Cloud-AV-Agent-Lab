from __future__ import annotations

import json
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.adapters.cloud import CloudProviderError
from cloud_av_agent_lab.adapters.tencent_cloud import (
    TencentCloudApiError,
    TencentCloudLighthouseAdapter,
    build_tc3_headers,
    parse_lighthouse_instance_status,
    resolve_tencent_cloud_auth,
)
from cloud_av_agent_lab.config import load_config
from cloud_av_agent_lab.core.pipeline import TestPipeline
from cloud_av_agent_lab.network.client import NetworkResponse


class FakeNetworkClient:
    def __init__(self, body: bytes | list[bytes]) -> None:
        self.bodies = [body] if isinstance(body, bytes) else list(body)
        self.proxy_map: dict[str, str] = {}
        self.calls: list[dict[str, object]] = []

    def request_json(self, **kwargs: object) -> NetworkResponse:
        self.calls.append(kwargs)
        if len(self.bodies) > 1:
            body = self.bodies.pop(0)
        else:
            body = self.bodies[0]
        return NetworkResponse(status=200, headers={}, body=body)


def _describe_instances_response() -> dict[str, object]:
    fixture = ROOT / "tests" / "fixtures" / "tencent_lighthouse_describe_instances.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    return payload["Response"]


def _api_response(response: dict[str, object]) -> bytes:
    return json.dumps({"Response": response}, ensure_ascii=False).encode("utf-8")


def _describe_response_with_state(
    state: str,
    latest_operation: str = "StartInstances",
    latest_operation_state: str = "SUCCESS",
    latest_operation_request_id: str = "request-id-001",
) -> dict[str, object]:
    response = deepcopy(_describe_instances_response())
    instance_set = response["InstanceSet"]
    assert isinstance(instance_set, list)
    instance = instance_set[0]
    instance["InstanceState"] = state
    instance["LatestOperation"] = latest_operation
    instance["LatestOperationState"] = latest_operation_state
    instance["LatestOperationRequestId"] = latest_operation_request_id
    return response


class TencentCloudAdapterPreparationTests(TestCase):
    def test_environment_variables_override_config_credentials(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(
            config.cloud,
            secret_id="config-secret-id",
            secret_key="config-secret-key",
            region="ap-guangzhou",
        )

        auth = resolve_tencent_cloud_auth(
            cloud,
            {
                "TENCENTCLOUD_SECRET_ID": "env-secret-id",
                "TENCENTCLOUD_SECRET_KEY": "env-secret-key",
                "TENCENTCLOUD_REGION": "ap-shanghai",
            },
        )

        self.assertEqual(auth.secret_id, "env-secret-id")
        self.assertEqual(auth.secret_key, "env-secret-key")
        self.assertEqual(auth.region, "ap-shanghai")
        self.assertEqual(auth.secret_id_source, "env")
        self.assertEqual(auth.secret_key_source, "env")
        self.assertEqual(auth.region_source, "env")

    def test_config_is_used_when_environment_is_empty(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(
            config.cloud,
            secret_id="config-secret-id",
            secret_key="config-secret-key",
            region="ap-guangzhou",
        )

        auth = resolve_tencent_cloud_auth(cloud, {})

        self.assertEqual(auth.secret_id, "config-secret-id")
        self.assertEqual(auth.secret_key, "config-secret-key")
        self.assertEqual(auth.region, "ap-guangzhou")
        self.assertEqual(auth.secret_id_source, "config")

    def test_real_mode_dry_run_does_not_call_api(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(config.cloud, mode="real", dry_run=True)
        adapter = TencentCloudLighthouseAdapter(cloud, env={})
        adapter._call_api = Mock(side_effect=AssertionError("should not call API"))

        response = adapter.start_vm(config.vms["win10-tencent-manager"])

        adapter._call_api.assert_not_called()
        self.assertEqual(response.status, "dry-run")
        self.assertTrue(response.dry_run)
        self.assertEqual(response.action, "StartInstances")
        self.assertIn("[DRY-RUN] Would call: StartInstances", response.message)

    def test_real_write_without_adapter_confirmation_stays_dry_run(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(
            config.cloud,
            mode="real",
            dry_run=False,
            secret_id="AKIDEXAMPLE",
            secret_key="SECRET",
        )
        adapter = TencentCloudLighthouseAdapter(cloud, env={})
        adapter._call_api = Mock(side_effect=AssertionError("should not call API"))

        response = adapter.start_vm(config.vms["win10-tencent-manager"])

        adapter._call_api.assert_not_called()
        self.assertEqual(response.status, "dry-run")
        self.assertIn("write confirmation missing or mismatched", response.message)

    def test_restore_snapshot_dry_run_does_not_call_api(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(config.cloud, mode="real", dry_run=True)
        adapter = TencentCloudLighthouseAdapter(cloud, env={})
        adapter._call_api = Mock(side_effect=AssertionError("should not call API"))

        response = adapter.restore_snapshot(config.vms["win10-tencent-manager"])

        adapter._call_api.assert_not_called()
        self.assertEqual(response.status, "dry-run")
        self.assertEqual(response.action, "ApplyInstanceSnapshot")
        self.assertIn("[DRY-RUN] Would call: ApplyInstanceSnapshot", response.message)

    def test_restore_snapshot_without_snapshot_confirmation_stays_dry_run(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(
            config.cloud,
            mode="real",
            dry_run=False,
            secret_id="AKIDEXAMPLE",
            secret_key="SECRET",
        )
        adapter = TencentCloudLighthouseAdapter(
            cloud,
            env={
                "TENCENTCLOUD_INSTANCE_ID_WIN10_TENCENT_MANAGER": "lhins-example",
            },
            confirmed_instance_id="lhins-example",
            confirmed_snapshot_id="wrong-snapshot",
        )
        adapter._call_api = Mock(side_effect=AssertionError("should not call API"))

        response = adapter.restore_snapshot(config.vms["win10-tencent-manager"])

        adapter._call_api.assert_not_called()
        self.assertEqual(response.status, "dry-run")
        self.assertIn("restore confirmation missing or mismatched", response.message)

    def test_instance_id_environment_overrides_config(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        adapter = TencentCloudLighthouseAdapter(
            config.cloud,
            env={
                "TENCENTCLOUD_INSTANCE_ID_WIN10_TENCENT_MANAGER": "lhins-from-env",
            },
        )

        response = adapter.start_vm(config.vms["win10-tencent-manager"])

        self.assertEqual(response.params["InstanceIds"], ["lhins-from-env"])

    def test_tc3_headers_include_lighthouse_service_scope(self) -> None:
        headers = build_tc3_headers(
            secret_id="AKIDEXAMPLE",
            secret_key="SECRET",
            endpoint="https://lighthouse.tencentcloudapi.com",
            action="DescribeInstances",
            version="2020-03-24",
            region="ap-guangzhou",
            payload={"InstanceIds": ["lhins-example"]},
            timestamp=1700000000,
        )

        self.assertEqual(headers["Host"], "lighthouse.tencentcloudapi.com")
        self.assertEqual(headers["X-TC-Action"], "DescribeInstances")
        self.assertEqual(headers["X-TC-Version"], "2020-03-24")
        self.assertIn("/lighthouse/tc3_request", headers["Authorization"])

    def test_describe_instances_response_parses_structured_status(self) -> None:
        status = parse_lighthouse_instance_status(
            _describe_instances_response(),
            expected_instance_id="lhins-example",
        )

        self.assertEqual(status.instance_id, "lhins-example")
        self.assertEqual(status.name, "redacted-windows-server")
        self.assertEqual(status.state, "RUNNING")
        self.assertTrue(status.known_state)
        self.assertTrue(status.control_plane_ready)
        self.assertTrue(status.guest_access_ready)
        self.assertTrue(status.can_stop)
        self.assertTrue(status.can_reboot)
        self.assertFalse(status.can_start)
        self.assertFalse(status.can_restore_snapshot)
        self.assertEqual(status.private_ipv4, ("10.3.4.9",))
        self.assertEqual(status.public_ipv4, ("203.0.113.10",))
        self.assertTrue(status.public_ipv4_assigned)
        self.assertEqual(status.request_id, "ddb9f215-b27f-4460-9766-0124a5da054b")
        self.assertEqual(status.operation_allowed()["guest_access"], True)

    def test_describe_instances_unknown_state_blocks_operations(self) -> None:
        payload = deepcopy(_describe_instances_response())
        instance_set = payload["InstanceSet"]
        self.assertIsInstance(instance_set, list)
        instance_set[0]["InstanceState"] = "MIGRATING"

        status = parse_lighthouse_instance_status(
            payload,
            expected_instance_id="lhins-example",
        )

        self.assertFalse(status.known_state)
        self.assertFalse(status.guest_access_ready)
        self.assertFalse(status.operation_allowed()["stop"])
        self.assertIn("unknown Lighthouse instance state", status.blocked_reason)

    def test_describe_instances_missing_expected_instance_raises(self) -> None:
        with self.assertRaises(CloudProviderError):
            parse_lighthouse_instance_status(
                _describe_instances_response(),
                expected_instance_id="lhins-missing",
            )

    def test_real_mode_calls_network_with_signed_headers(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(
            config.cloud,
            mode="real",
            dry_run=False,
            secret_id="AKIDEXAMPLE",
            secret_key="SECRET",
        )
        network = FakeNetworkClient(_api_response(_describe_instances_response()))
        adapter = TencentCloudLighthouseAdapter(
            cloud,
            network=network,
            env={
                "TENCENTCLOUD_INSTANCE_ID_WIN10_TENCENT_MANAGER": "lhins-example",
            },
        )

        response = adapter.get_instance_status(config.vms["win10-tencent-manager"])

        self.assertEqual(response.status, "success")
        self.assertEqual(response.task_id, "ddb9f215-b27f-4460-9766-0124a5da054b")
        self.assertEqual(
            response.data["RequestId"],
            "ddb9f215-b27f-4460-9766-0124a5da054b",
        )
        self.assertEqual(response.data["InstanceStatus"]["state"], "RUNNING")
        self.assertIn("guest_access_ready=True", response.message)
        self.assertEqual(network.calls[0]["method"], "POST")
        self.assertEqual(
            network.calls[0]["url"],
            "https://lighthouse.tencentcloudapi.com",
        )
        headers = network.calls[0]["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["X-TC-Action"], "DescribeInstances")
        self.assertIn("TC3-HMAC-SHA256", headers["Authorization"])

    def test_real_start_calls_write_api_then_polls_until_running(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(
            config.cloud,
            mode="real",
            dry_run=False,
            secret_id="AKIDEXAMPLE",
            secret_key="SECRET",
        )
        network = FakeNetworkClient(
            [
                _api_response({"RequestId": "start-request-001"}),
                _api_response(
                    _describe_response_with_state(
                        "RUNNING",
                        latest_operation="StartInstances",
                        latest_operation_request_id="start-request-001",
                    )
                ),
            ]
        )
        adapter = TencentCloudLighthouseAdapter(
            cloud,
            network=network,
            env={
                "TENCENTCLOUD_INSTANCE_ID_WIN10_TENCENT_MANAGER": "lhins-example",
            },
            confirmed_instance_id="lhins-example",
            poll_timeout_seconds=1,
            poll_interval_seconds=0,
        )

        with self.assertLogs(
            "cloud_av_agent_lab.adapters.tencent_cloud",
            level="INFO",
        ) as logs:
            response = adapter.start_vm(config.vms["win10-tencent-manager"])

        self.assertEqual(response.status, "success")
        self.assertEqual(response.task_id, "start-request-001")
        self.assertEqual(response.data["FinalInstanceStatus"]["state"], "RUNNING")
        self.assertIn("reached RUNNING", response.message)
        self.assertIn(
            "API Request Accepted, RequestId: start-request-001",
            "\n".join(logs.output),
        )
        self.assertIn(
            "Polling instance lhins-example: state=RUNNING", "\n".join(logs.output)
        )
        self.assertIn("waited=", "\n".join(logs.output))
        self.assertEqual(len(network.calls), 2)
        first_headers = network.calls[0]["headers"]
        second_headers = network.calls[1]["headers"]
        self.assertEqual(first_headers["X-TC-Action"], "StartInstances")
        self.assertEqual(second_headers["X-TC-Action"], "DescribeInstances")

    def test_wait_instance_status_raises_on_failed_latest_operation(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(
            config.cloud,
            mode="real",
            dry_run=False,
            secret_id="AKIDEXAMPLE",
            secret_key="SECRET",
        )
        network = FakeNetworkClient(
            _api_response(
                _describe_response_with_state(
                    "RUNNING",
                    latest_operation="StartInstances",
                    latest_operation_state="FAILED",
                )
            )
        )
        adapter = TencentCloudLighthouseAdapter(
            cloud,
            network=network,
            env={
                "TENCENTCLOUD_INSTANCE_ID_WIN10_TENCENT_MANAGER": "lhins-example",
            },
            poll_timeout_seconds=1,
            poll_interval_seconds=0,
        )

        with self.assertRaises(CloudProviderError) as error:
            adapter.wait_instance_status(
                config.vms["win10-tencent-manager"],
                target_state="RUNNING",
            )

        self.assertIn("operation failed", str(error.exception))

    def test_restore_snapshot_rejects_running_instance_before_write(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        vm = config.vms["win10-tencent-manager"]
        cloud = replace(
            config.cloud,
            mode="real",
            dry_run=False,
            secret_id="AKIDEXAMPLE",
            secret_key="SECRET",
        )
        network = FakeNetworkClient(
            _api_response(
                _describe_response_with_state(
                    "RUNNING",
                    latest_operation="StartInstances",
                    latest_operation_request_id="start-request-001",
                )
            )
        )
        adapter = TencentCloudLighthouseAdapter(
            cloud,
            network=network,
            env={
                "TENCENTCLOUD_INSTANCE_ID_WIN10_TENCENT_MANAGER": "lhins-example",
            },
            confirmed_instance_id="lhins-example",
            confirmed_snapshot_id=vm.baseline_snapshot,
            poll_timeout_seconds=1,
            poll_interval_seconds=0,
        )

        with self.assertRaises(CloudProviderError) as error:
            adapter.restore_snapshot(vm)

        self.assertIn("please stop the instance first", str(error.exception))
        self.assertEqual(len(network.calls), 1)
        headers = network.calls[0]["headers"]
        self.assertEqual(headers["X-TC-Action"], "DescribeInstances")

    def test_restore_snapshot_calls_api_then_starts_until_running(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        vm = config.vms["win10-tencent-manager"]
        cloud = replace(
            config.cloud,
            mode="real",
            dry_run=False,
            secret_id="AKIDEXAMPLE",
            secret_key="SECRET",
        )
        network = FakeNetworkClient(
            [
                _api_response(
                    _describe_response_with_state(
                        "STOPPED",
                        latest_operation="StopInstances",
                        latest_operation_request_id="stop-request-001",
                    )
                ),
                _api_response({"RequestId": "restore-request-001"}),
                _api_response(
                    _describe_response_with_state(
                        "STOPPED",
                        latest_operation="ApplyInstanceSnapshot",
                        latest_operation_request_id="restore-request-001",
                    )
                ),
                _api_response({"RequestId": "start-request-002"}),
                _api_response(
                    _describe_response_with_state(
                        "RUNNING",
                        latest_operation="StartInstances",
                        latest_operation_request_id="start-request-002",
                    )
                ),
            ]
        )
        adapter = TencentCloudLighthouseAdapter(
            cloud,
            network=network,
            env={
                "TENCENTCLOUD_INSTANCE_ID_WIN10_TENCENT_MANAGER": "lhins-example",
            },
            confirmed_instance_id="lhins-example",
            confirmed_snapshot_id=vm.baseline_snapshot,
            poll_timeout_seconds=1,
            poll_interval_seconds=0,
        )

        with self.assertLogs(
            "cloud_av_agent_lab.adapters.tencent_cloud",
            level="INFO",
        ) as logs:
            response = adapter.restore_snapshot(vm)

        self.assertEqual(response.status, "success")
        self.assertEqual(response.task_id, "restore-request-001")
        self.assertEqual(response.data["PrecheckInstanceStatus"]["state"], "STOPPED")
        self.assertEqual(
            response.data["PostRestoreInstanceStatus"]["latest_operation"],
            "ApplyInstanceSnapshot",
        )
        self.assertEqual(response.data["FinalInstanceStatus"]["state"], "RUNNING")
        self.assertEqual(len(network.calls), 5)
        actions = [call["headers"]["X-TC-Action"] for call in network.calls]
        self.assertEqual(
            actions,
            [
                "DescribeInstances",
                "ApplyInstanceSnapshot",
                "DescribeInstances",
                "StartInstances",
                "DescribeInstances",
            ],
        )
        joined_logs = "\n".join(logs.output)
        self.assertIn(
            "API Request Accepted, RequestId: restore-request-001", joined_logs
        )
        self.assertIn("API Request Accepted, RequestId: start-request-002", joined_logs)
        self.assertIn("snapshot restored and reached RUNNING", response.message)

    def test_real_mode_raises_api_error_response(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(
            config.cloud,
            mode="real",
            dry_run=False,
            secret_id="AKIDEXAMPLE",
            secret_key="SECRET",
        )
        network = FakeNetworkClient(
            b'{"Response":{"Error":{"Code":"UnauthorizedOperation.NoPermission",'
            b'"Message":"no permission"},"RequestId":"request-id-002"}}',
        )
        adapter = TencentCloudLighthouseAdapter(cloud, network=network, env={})

        with self.assertRaises(TencentCloudApiError) as error:
            adapter.get_instance_status(config.vms["win10-tencent-manager"])

        self.assertEqual(error.exception.code, "UnauthorizedOperation.NoPermission")
        self.assertEqual(error.exception.request_id, "request-id-002")

    def test_pipeline_plan_forces_dry_run_even_when_config_allows_real(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        config = replace(
            config,
            cloud=replace(
                config.cloud,
                mode="real",
                dry_run=False,
                secret_id="AKIDEXAMPLE",
                secret_key="SECRET",
            ),
        )

        events = TestPipeline(config).dry_run()

        self.assertIn("[DRY-RUN] Would call: StartInstances", "\n".join(events))
