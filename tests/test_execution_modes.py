from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.core.execution_modes import (  # noqa: E402
    ExecutionPolicy,
    resolve_execution_mode,
)


class ExecutionModeTests(unittest.TestCase):
    def test_exe_resolves_to_pe_executable(self) -> None:
        decision = resolve_execution_mode("proof.exe")

        self.assertTrue(decision.enabled)
        self.assertEqual(decision.handler_id, "pe_executable")
        self.assertEqual(decision.execution_mode, "direct_process")

    def test_batch_and_cmd_resolve_to_batch_handler(self) -> None:
        for filename in ("eicar.bat", "eicar.cmd"):
            with self.subTest(filename=filename):
                decision = resolve_execution_mode(filename)

                self.assertTrue(decision.enabled)
                self.assertEqual(decision.handler_id, "batch_script")
                self.assertEqual(decision.execution_mode, "script_via_cmd")

    def test_powershell_is_recognized_but_disabled_by_default(self) -> None:
        decision = resolve_execution_mode("sample.ps1")

        self.assertFalse(decision.enabled)
        self.assertEqual(decision.handler_id, "powershell_script")
        self.assertEqual(decision.reason_code, "execution_handler_disabled")

    def test_batch_can_be_disabled_by_policy(self) -> None:
        decision = resolve_execution_mode(
            "sample.bat",
            ExecutionPolicy(allow_batch_script=False),
        )

        self.assertFalse(decision.enabled)
        self.assertEqual(decision.handler_id, "batch_script")
        self.assertEqual(decision.reason_code, "execution_handler_disabled")

    def test_unknown_suffix_is_unsupported(self) -> None:
        decision = resolve_execution_mode("sample.bin")

        self.assertFalse(decision.enabled)
        self.assertEqual(decision.handler_id, "unsupported")
        self.assertEqual(decision.reason_code, "unsupported_file_type")


if __name__ == "__main__":
    unittest.main()
