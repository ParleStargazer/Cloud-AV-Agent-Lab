from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.adapters.tencent_cloud import TencentCloudLighthouseAdapter
from cloud_av_agent_lab.config import load_config
from cloud_av_agent_lab.network.client import NetworkClient


class TencentCloudLighthouseAdapterTests(TestCase):
    def test_adapter_initializes_with_network_client(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        network = NetworkClient.from_config(config.network)

        adapter = TencentCloudLighthouseAdapter(config.cloud, network=network)

        self.assertFalse(adapter.supports_execution)
        self.assertIs(adapter.network, network)
        self.assertEqual(adapter.cloud.provider, "tencent-cloud-lighthouse")

    def test_adapter_describes_stub_operations(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        adapter = TencentCloudLighthouseAdapter(config.cloud)
        vm = config.vms["win10-tencent-manager"]

        event = adapter.start_vm(vm)

        self.assertEqual(event.status, "dry-run")
        self.assertIn("StartInstances", event.message)
        self.assertIn("lhins-replace-tencent-manager", str(event.params))
