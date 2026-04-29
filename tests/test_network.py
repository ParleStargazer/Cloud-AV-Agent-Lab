from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.core.contracts import ProxyConfig
from cloud_av_agent_lab.network.client import NetworkClient


class NetworkClientTests(TestCase):
    def test_disabled_proxy_does_not_inject_proxy_map(self) -> None:
        client = NetworkClient(
            ProxyConfig(
                enabled=False,
                type="socks5",
                host="127.0.0.1",
                port=7890,
            )
        )

        self.assertIsNone(client.proxy_url)
        self.assertEqual(client.proxy_map, {})

    def test_enabled_socks5_proxy_builds_shared_proxy_map(self) -> None:
        client = NetworkClient(
            ProxyConfig(
                enabled=True,
                type="socks5",
                host="127.0.0.1",
                port=7890,
            )
        )

        self.assertEqual(client.proxy_url, "socks5://127.0.0.1:7890")
        self.assertEqual(
            client.proxy_map,
            {
                "http": "socks5://127.0.0.1:7890",
                "https": "socks5://127.0.0.1:7890",
            },
        )
