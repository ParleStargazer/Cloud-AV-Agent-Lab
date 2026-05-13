from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.config import load_config
from cloud_av_agent_lab.core.pipeline import TestPipeline
from cloud_av_agent_lab.core.safety import assert_safe_config


class ConfigTests(TestCase):
    def test_example_config_is_safe_and_builds_matrix(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        assert_safe_config(config)

        cases = TestPipeline(config).build_plan()

        self.assertEqual(len(cases), 6)
        self.assertEqual(cases[0].sample.id, "case-001")
        self.assertIn(cases[0].product.id, config.products)
        self.assertFalse(config.network.proxy.enabled)
        self.assertFalse(config.guest_agent.enabled)
        self.assertEqual(config.guest_agent.token_env, "CLOUD_AV_GUEST_AGENT_TOKEN")

    def test_enabled_proxy_config_parses(self) -> None:
        config_text = (ROOT / "configs" / "lab.example.toml").read_text(
            encoding="utf-8"
        )
        enabled_config = config_text.replace("enabled = false", "enabled = true")

        tmp_dir = ROOT / "state" / "tests"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        config_path = tmp_dir / "lab.proxy-enabled.toml"
        config_path.write_text(enabled_config, encoding="utf-8")
        try:
            config = load_config(config_path)
        finally:
            config_path.unlink(missing_ok=True)

        self.assertTrue(config.network.proxy.enabled)
        self.assertEqual(config.network.proxy.type, "socks5")
        self.assertEqual(config.network.proxy.host, "127.0.0.1")
        self.assertEqual(config.network.proxy.port, 7890)

    def test_enabled_guest_agent_config_parses(self) -> None:
        config_text = (ROOT / "configs" / "lab.example.toml").read_text(
            encoding="utf-8"
        )
        enabled_config = config_text.replace(
            "[guest_agent]\n# 云端隔离 Windows 主机内的 Guest Agent。MVP 只做连通性和环境准备，\n"
            "# 不投递、不下载、不执行样本。\n"
            "enabled = false",
            "[guest_agent]\n# 云端隔离 Windows 主机内的 Guest Agent。MVP 只做连通性和环境准备，\n"
            "# 不投递、不下载、不执行样本。\n"
            "enabled = true",
        )

        tmp_dir = ROOT / "state" / "tests"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        config_path = tmp_dir / "lab.guest-agent-enabled.toml"
        config_path.write_text(enabled_config, encoding="utf-8")
        try:
            config = load_config(config_path)
        finally:
            config_path.unlink(missing_ok=True)

        self.assertTrue(config.guest_agent.enabled)
        self.assertEqual(config.guest_agent.base_url, "http://127.0.0.1:8080")
        self.assertEqual(config.guest_agent.token_env, "CLOUD_AV_GUEST_AGENT_TOKEN")
        self.assertEqual(config.guest_agent.timeout_seconds, 10)
