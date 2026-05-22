from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.guest_agent_server.collectors import (
    get_product_log_collector,
    supported_product_log_collectors,
)
from cloud_av_agent_lab.guest_agent_server.collectors.huorong import HuorongLogCollector
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

    def test_registry_returns_none_for_unknown_product(self) -> None:
        self.assertIsNone(get_product_log_collector("unknown-product"))

    def test_workspace_collector_error_lists_registry_products(self) -> None:
        with self.assertRaises(WorkspaceError) as error:
            _collector_for("unknown-product")

        message = str(error.exception)
        self.assertIn("unknown-product", message)
        self.assertIn("available collectors: huorong", message)
