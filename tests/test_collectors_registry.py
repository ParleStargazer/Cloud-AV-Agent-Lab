from __future__ import annotations

import builtins
import importlib
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.guest_agent_server.collectors import (
    get_product_log_collector,
    supported_product_log_collectors,
)
from cloud_av_agent_lab.guest_agent_server.collectors.huorong import HuorongLogCollector
from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
    Qihoo360LogCollector,
)
from cloud_av_agent_lab.guest_agent_server.collectors.windows_defender import (
    WindowsDefenderLogCollector,
)
from cloud_av_agent_lab.guest_agent_server.workspace.collection import _collector_for
from cloud_av_agent_lab.guest_agent_server.workspace.errors import WorkspaceError


class CollectorRegistryTests(TestCase):
    def test_registry_returns_huorong_collector(self) -> None:
        collector = get_product_log_collector("huorong")

        self.assertIsInstance(collector, HuorongLogCollector)
        self.assertIn("huorong", supported_product_log_collectors())

    def test_registry_normalizes_product_id(self) -> None:
        collector = get_product_log_collector("  HUORONG  ")

        self.assertIsInstance(collector, HuorongLogCollector)

    def test_registry_returns_windows_defender_collector(self) -> None:
        collector = get_product_log_collector("windows-defender")

        self.assertIsInstance(collector, WindowsDefenderLogCollector)
        self.assertIn("windows-defender", supported_product_log_collectors())

    def test_registry_returns_qihoo_360_collector(self) -> None:
        collector = get_product_log_collector("qihoo-360")

        self.assertIsInstance(collector, Qihoo360LogCollector)
        self.assertIn("qihoo-360", supported_product_log_collectors())

    def test_registry_import_does_not_require_pywin32(self) -> None:
        from cloud_av_agent_lab.guest_agent_server.collectors import registry

        original_import = builtins.__import__
        attempted_pywin32_imports: list[str] = []

        def guarded_import(
            name: str,
            globals: object | None = None,
            locals: object | None = None,
            fromlist: tuple[object, ...] = (),
            level: int = 0,
        ) -> object:
            if name in {"pywintypes", "win32evtlog"}:
                attempted_pywin32_imports.append(name)
                raise AssertionError(f"registry import attempted {name}")
            return original_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=guarded_import):
            reloaded = importlib.reload(registry)

        self.assertIn("windows-defender", reloaded.supported_product_log_collectors())
        self.assertEqual(attempted_pywin32_imports, [])

    def test_registry_returns_none_for_unknown_product(self) -> None:
        self.assertIsNone(get_product_log_collector("unknown-product"))

    def test_workspace_collector_error_lists_registry_products(self) -> None:
        with self.assertRaises(WorkspaceError) as error:
            _collector_for("unknown-product")

        message = str(error.exception)
        self.assertIn("unknown-product", message)
        self.assertIn("huorong", message)
        self.assertIn("qihoo-360", message)
        self.assertIn("windows-defender", message)
