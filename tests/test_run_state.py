from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_av_agent_lab.orchestration.run_state import RunState


class RunStateWriteTests(unittest.TestCase):
    def test_write_retries_transient_replace_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_state_path = Path(tmp) / "run_state.json"
            state = RunState(
                run_state_path,
                run_id="run-001",
                case_id="case-001",
                instance_id="lhins-example",
                snapshot_id="lhsnap-example",
                region="ap-singapore",
                product_id="huorong",
                sample_name="sample",
                sample_path="sample.exe",
            )
            original_replace = Path.replace
            calls = {"permission_errors": 0}

            def flaky_replace(self: Path, target: Path | str) -> Path:
                if (
                    self.name.startswith(".run_state.json.")
                    and Path(target).name == "run_state.json"
                    and calls["permission_errors"] == 0
                ):
                    calls["permission_errors"] += 1
                    raise PermissionError(5, "Access is denied")
                return original_replace(self, target)

            with (
                patch.object(Path, "replace", new=flaky_replace),
                patch("cloud_av_agent_lab.orchestration.run_state.time.sleep") as sleep,
            ):
                state.mark("status", "ok")

            self.assertEqual(calls["permission_errors"], 1)
            self.assertTrue(sleep.called)
            payload = json.loads(run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(list(Path(tmp).glob(".run_state.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
