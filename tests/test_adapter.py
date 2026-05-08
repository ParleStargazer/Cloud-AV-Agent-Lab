from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.adapters.tencent_cloud import (
    TencentCloudApiError,
    TencentCloudLighthouseAdapter,
    build_tc3_headers,
    resolve_tencent_cloud_auth,
)
from cloud_av_agent_lab.config import load_config
from cloud_av_agent_lab.core.pipeline import TestPipeline
from cloud_av_agent_lab.network.client import NetworkResponse


class FakeNetworkClient:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.proxy_map: dict[str, str] = {}
        self.calls: list[dict[str, object]] = []

    def request_json(self, **kwargs: object) -> NetworkResponse:
        self.calls.append(kwargs)
        return NetworkResponse(status=200, headers={}, body=self.body)


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

    def test_real_mode_calls_network_with_signed_headers(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        cloud = replace(
            config.cloud,
            mode="real",
            dry_run=False,
            secret_id="AKIDEXAMPLE",
            secret_key="SECRET",
        )
        network = FakeNetworkClient(
            b'{"Response":{"RequestId":"request-id-001"}}',
        )
        adapter = TencentCloudLighthouseAdapter(cloud, network=network, env={})

        response = adapter.get_instance_status(config.vms["win10-tencent-manager"])

        self.assertEqual(response.status, "success")
        self.assertEqual(response.task_id, "request-id-001")
        self.assertEqual(response.data["RequestId"], "request-id-001")
        self.assertEqual(network.calls[0]["method"], "POST")
        self.assertEqual(
            network.calls[0]["url"],
            "https://lighthouse.tencentcloudapi.com",
        )
        headers = network.calls[0]["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["X-TC-Action"], "DescribeInstances")
        self.assertIn("TC3-HMAC-SHA256", headers["Authorization"])

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
