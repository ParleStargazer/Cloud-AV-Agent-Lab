from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.adapters.tencent_cloud import (
    TencentCloudLighthouseAdapter,
    resolve_tencent_cloud_auth,
)
from cloud_av_agent_lab.config import load_config


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
