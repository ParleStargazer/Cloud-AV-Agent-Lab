from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.cli import _write_execution_guard
from cloud_av_agent_lab.config import load_config


class CloudLifecycleCliGuardTests(TestCase):
    def test_write_guard_requires_real_mode_dry_run_false_and_confirm_match(
        self,
    ) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        config = replace(
            config,
            cloud=replace(config.cloud, mode="real", dry_run=False),
        )

        allowed, reasons = _write_execution_guard(
            config,
            resolved_instance_id="lhins-example",
            confirm_instance="lhins-example",
        )

        self.assertTrue(allowed)
        self.assertEqual(reasons, [])

    def test_write_guard_blocks_missing_or_wrong_confirmation(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        config = replace(
            config,
            cloud=replace(config.cloud, mode="real", dry_run=False),
        )

        missing_allowed, missing_reasons = _write_execution_guard(
            config,
            resolved_instance_id="lhins-example",
            confirm_instance="",
        )
        wrong_allowed, wrong_reasons = _write_execution_guard(
            config,
            resolved_instance_id="lhins-example",
            confirm_instance="lhins-other",
        )

        self.assertFalse(missing_allowed)
        self.assertIn("--confirm-instance was not provided", missing_reasons)
        self.assertFalse(wrong_allowed)
        self.assertIn(
            "--confirm-instance does not match resolved instance id",
            wrong_reasons,
        )

    def test_write_guard_blocks_dry_run_config(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        config = replace(
            config,
            cloud=replace(config.cloud, mode="real", dry_run=True),
        )

        allowed, reasons = _write_execution_guard(
            config,
            resolved_instance_id="lhins-example",
            confirm_instance="lhins-example",
        )

        self.assertFalse(allowed)
        self.assertIn("cloud dry_run is true", reasons)
