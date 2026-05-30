from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from cloud_av_agent_lab.guest_agent_server.collectors.tencent_pc_manager import (
    assess_container_size_delta,
    normalize_tav_md5,
    observe_tav_quarantine,
)
from cloud_av_agent_lab.guest_agent_server.collectors.tencent_pc_manager import (
    quarantine as tav_quarantine_module,
)


class TencentPcManagerQuarantineParserTests(unittest.TestCase):
    def test_md5_filename_container_is_recognized_with_strong_size_delta(self) -> None:
        sample_md5 = "BD3F9E29EC9ECAFC9B8B2475AFB3A9A2"
        with tempfile.TemporaryDirectory() as tmp:
            quarantine_dir = Path(tmp)
            container = quarantine_dir / sample_md5.casefold()
            container.write_bytes(b"x" * 1016)
            (quarantine_dir / f"{sample_md5.casefold()}.ico").write_bytes(
                b"icon placeholder"
            )

            observation = observe_tav_quarantine(
                quarantine_dir,
                sample_md5=sample_md5,
                original_sample_size=1000,
            )

        self.assertEqual(observation.sample_md5, sample_md5.casefold())
        self.assertTrue(observation.container_present)
        self.assertTrue(observation.icon_sidecar_present)
        self.assertEqual(observation.container.name, sample_md5.casefold())
        self.assertEqual(observation.container.size, 1016)
        self.assertEqual(observation.size_delta.delta, 16)
        self.assertEqual(observation.size_delta.level, "strong")
        self.assertEqual(observation.evidence_level, "strong")
        self.assertEqual(observation.warnings, ())

    def test_icon_sidecar_without_container_is_weak_not_strong(self) -> None:
        sample_md5 = "bd3f9e29ec9ecafc9b8b2475afb3a9a2"
        with tempfile.TemporaryDirectory() as tmp:
            quarantine_dir = Path(tmp)
            (quarantine_dir / f"{sample_md5}.ico").write_bytes(b"icon placeholder")

            observation = observe_tav_quarantine(
                quarantine_dir,
                sample_md5=sample_md5,
                original_sample_size=1000,
            )

        self.assertFalse(observation.container_present)
        self.assertTrue(observation.icon_sidecar_present)
        self.assertEqual(observation.evidence_level, "weak")
        self.assertIn(
            "quarantine_icon_sidecar_without_container",
            observation.warnings,
        )

    def test_negative_small_size_delta_is_medium(self) -> None:
        assessment = assess_container_size_delta(
            observed_size=900,
            original_size=1000,
        )

        self.assertEqual(assessment.delta, -100)
        self.assertEqual(assessment.level, "medium")
        self.assertIn("quarantine_container_size_delta_negative", assessment.warnings)

    def test_out_of_range_size_delta_warns_without_failing(self) -> None:
        assessment = assess_container_size_delta(
            observed_size=9000,
            original_size=1000,
        )

        self.assertEqual(assessment.delta, 8000)
        self.assertEqual(assessment.level, "weak")
        self.assertIn(
            "quarantine_container_size_delta_out_of_range",
            assessment.warnings,
        )

    def test_missing_original_size_is_unknown_but_nonfatal(self) -> None:
        assessment = assess_container_size_delta(
            observed_size=1016,
            original_size=None,
        )

        self.assertIsNone(assessment.delta)
        self.assertEqual(assessment.level, "unknown")
        self.assertIn("original_sample_size_missing", assessment.warnings)

    def test_invalid_md5_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "md5"):
            normalize_tav_md5("not-md5")

    def test_parser_is_stat_only_and_has_no_execution_paths(self) -> None:
        source = inspect.getsource(tav_quarantine_module)

        self.assertNotIn("read_bytes", source)
        self.assertNotIn("read_text", source)
        self.assertNotIn(".open(", source)
        self.assertNotIn("QMQuarantine", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)


if __name__ == "__main__":
    unittest.main()
