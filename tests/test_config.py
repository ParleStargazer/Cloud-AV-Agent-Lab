from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.config import ConfigError, load_config
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
        self.assertFalse(config.guest_agent.execution.enabled)
        self.assertEqual(
            config.guest_agent.execution.token_env,
            "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN",
        )
        self.assertEqual(config.guest_agent.execution.timeout_seconds, 30)
        self.assertFalse(config.guest_agent.desktop_worker.enabled)
        self.assertEqual(
            config.guest_agent.desktop_worker.token_env,
            "CLOUD_AV_DESKTOP_WORKER_TOKEN",
        )
        self.assertEqual(
            config.guest_agent.desktop_worker.base_url,
            "http://127.0.0.1:8001",
        )
        self.assertTrue(config.guest_agent.desktop_worker.required_for_execution)
        self.assertTrue(config.guest_agent.desktop_worker.require_interactive_session)

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
            "[guest_agent]\n"
            "# 云端隔离 Windows 主机内的 Guest Agent。默认只做连通性、环境准备、\n"
            "# EICAR/无害文件上传、状态观测、受控触发和执行观测摘要；真实触发需在\n"
            "# [guest_agent.execution] 中显式开启。\n"
            "enabled = false",
            "[guest_agent]\n"
            "# 云端隔离 Windows 主机内的 Guest Agent。默认只做连通性、环境准备、\n"
            "# EICAR/无害文件上传、状态观测、受控触发和执行观测摘要；真实触发需在\n"
            "# [guest_agent.execution] 中显式开启。\n"
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

    def test_enabled_guest_agent_execution_requires_token_env(self) -> None:
        config_text = (ROOT / "configs" / "lab.example.toml").read_text(
            encoding="utf-8"
        )
        enabled_config = config_text.replace(
            "[guest_agent.execution]\n"
            "# 受控触发能力默认关闭。本地配置和云端 Guest Agent 都显式开启，并提供\n"
            "# execution token 后，才允许触发当前 case 已登记上传文件。\n"
            "# 不接受任意命令、任意路径或 shell 参数；本地控制面仍不执行样本。\n"
            "enabled = false\n"
            'token_env = "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"',
            "[guest_agent.execution]\n"
            "# 受控触发能力默认关闭。本地配置和云端 Guest Agent 都显式开启，并提供\n"
            "# execution token 后，才允许触发当前 case 已登记上传文件。\n"
            "# 不接受任意命令、任意路径或 shell 参数；本地控制面仍不执行样本。\n"
            "enabled = true\n"
            'token_env = ""',
        )

        tmp_dir = ROOT / "state" / "tests"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        config_path = tmp_dir / "lab.execution-token-missing.toml"
        config_path.write_text(enabled_config, encoding="utf-8")
        try:
            with self.assertRaisesRegex(ConfigError, "token_env"):
                load_config(config_path)
        finally:
            config_path.unlink(missing_ok=True)

    def test_enabled_desktop_worker_requires_token_env(self) -> None:
        config_text = (ROOT / "configs" / "lab.example.toml").read_text(
            encoding="utf-8"
        )
        enabled_config = config_text.replace(
            "[guest_agent.desktop_worker]\n"
            "# Desktop Worker 运行在云端 Windows 交互式桌面 session 中，只监听\n"
            "# 127.0.0.1。Control Agent 通过本机 HTTP 查询 Worker ready 状态；后续真实\n"
            "# 执行会下放给 Worker，避免 Session 0 直接启动样本。默认关闭，直到云端\n"
            "# baseline snapshot 已固化自动登录和 Worker 自启动。\n"
            "enabled = false\n"
            'base_url = "http://127.0.0.1:8001"\n'
            'token_env = "CLOUD_AV_DESKTOP_WORKER_TOKEN"',
            "[guest_agent.desktop_worker]\n"
            "# Desktop Worker 运行在云端 Windows 交互式桌面 session 中，只监听\n"
            "# 127.0.0.1。Control Agent 通过本机 HTTP 查询 Worker ready 状态；后续真实\n"
            "# 执行会下放给 Worker，避免 Session 0 直接启动样本。默认关闭，直到云端\n"
            "# baseline snapshot 已固化自动登录和 Worker 自启动。\n"
            "enabled = true\n"
            'base_url = "http://127.0.0.1:8001"\n'
            'token_env = ""',
        )

        tmp_dir = ROOT / "state" / "tests"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        config_path = tmp_dir / "lab.desktop-worker-token-missing.toml"
        config_path.write_text(enabled_config, encoding="utf-8")
        try:
            with self.assertRaisesRegex(ConfigError, "desktop_worker"):
                load_config(config_path)
        finally:
            config_path.unlink(missing_ok=True)
