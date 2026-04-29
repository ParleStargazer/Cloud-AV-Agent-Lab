from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.config import load_config
from cloud_av_agent_lab.core.pipeline import TestPipeline
from cloud_av_agent_lab.reporting.markdown import render_markdown_report


class ReportingTests(TestCase):
    def test_report_template_contains_detection_rate(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        pipeline = TestPipeline(config)

        report = render_markdown_report(config, pipeline.planned_results())

        self.assertIn("## Detection Rate", report)
        self.assertIn("Tencent PC Manager", report)
        self.assertIn("case-001__tencent-pc-manager", report)
