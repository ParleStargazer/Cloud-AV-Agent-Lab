from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cloud_av_agent_lab.orchestration.multi_run import (
    FakeSingleRunRunner,
    SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION,
    create_multi_run_batch_plan,
    execute_multi_run_batch,
    load_sample_manifest,
    parse_sample_selection,
    read_multi_run_events,
)


class MultiRunSerialSchedulerTests(unittest.TestCase):
    def test_executes_selected_samples_in_order_with_fake_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, runner = _plan_and_execute(tmp, indexes=(3, 1, 2))

            self.assertEqual(
                [request.sample_index for request in runner.requests],
                [1, 2, 3],
            )
            self.assertTrue((batch_dir / "cases" / ("0001_" + "a" * 16)).is_dir())
            self.assertTrue((batch_dir / "cases" / ("0002_" + "b" * 16)).is_dir())
            state = json.loads((batch_dir / "multi_run_state.json").read_text("utf-8"))
            self.assertEqual(state["batch_state"], "completed")
            self.assertEqual(
                [case["case_status"] for case in state["cases"]], ["completed"] * 3
            )

    def test_case_failure_default_policy_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, runner = _plan_and_execute(
                tmp,
                scenarios={1: "case_failed", 2: "completed"},
            )

            self.assertEqual(
                [request.sample_index for request in runner.requests], [1, 2]
            )
            state = json.loads((batch_dir / "multi_run_state.json").read_text("utf-8"))
            self.assertEqual(state["batch_state"], "completed_with_case_failures")
            self.assertEqual(state["cases"][0]["failure_kind"], "case_failure")
            self.assertEqual(state["cases"][1]["case_status"], "completed")

    def test_stop_on_case_failure_stops_after_failed_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, runner = _plan_and_execute(
                tmp,
                scenarios={1: "case_failed", 2: "completed"},
                failure_policy="stop-on-case-failure",
            )

            self.assertEqual([request.sample_index for request in runner.requests], [1])
            state = json.loads((batch_dir / "multi_run_state.json").read_text("utf-8"))
            self.assertEqual(state["batch_state"], "stopped_for_case_failure")
            self.assertEqual(state["cases"][0]["failure_kind"], "case_failure")
            self.assertEqual(state["cases"][1]["case_status"], "planned")

    def test_environment_failure_always_stops_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, runner = _plan_and_execute(
                tmp,
                scenarios={1: "environment_failed", 2: "completed"},
            )

            self.assertEqual([request.sample_index for request in runner.requests], [1])
            state = json.loads((batch_dir / "multi_run_state.json").read_text("utf-8"))
            self.assertEqual(state["batch_state"], "stopped_for_environment_failure")
            self.assertTrue(state["unsafe_to_continue"])
            self.assertTrue(state["manual_intervention_required"])
            self.assertEqual(state["cases"][0]["cleanup_status"], "restore_failed")
            self.assertEqual(state["cases"][1]["case_status"], "planned")

    def test_scheduler_writes_expected_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(tmp, indexes=(1,))

            events = read_multi_run_events(batch_dir / "multi_run_events.jsonl")
            event_types = [event["type"] for event in events]
            self.assertIn("preflight_started", event_types)
            self.assertIn("preflight_passed", event_types)
            self.assertIn("case_started", event_types)
            self.assertIn("single_run_started", event_types)
            self.assertIn("single_run_completed", event_types)
            self.assertIn("case_finalized", event_types)
            self.assertEqual(event_types[-1], "batch_finished")


def _plan_and_execute(
    tmp: str,
    *,
    indexes: tuple[int, ...] = (1, 2),
    scenarios: dict[int, str] | None = None,
    failure_policy: str = "continue",
) -> tuple[Path, FakeSingleRunRunner]:
    tmp_path = Path(tmp)
    manifest_path = tmp_path / "sample_manifest.jsonl"
    _write_manifest(manifest_path)
    manifest = load_sample_manifest(manifest_path)
    selection = parse_sample_selection(
        manifest.indexes,
        indexes_text=",".join(str(index) for index in indexes),
    )
    artifacts = create_multi_run_batch_plan(
        batch_root=tmp_path / "batches",
        batch_id="batch-test",
        product_id="huorong",
        instance_id="lhins-test",
        snapshot_id="lhsnap-test",
        region="ap-singapore",
        guest_agent_url="http://127.0.0.1:8080",
        desktop_worker_url="http://127.0.0.1:8001",
        manifest=manifest,
        selection=selection,
        dry_run=True,
        failure_policy=failure_policy,
    )
    runner = FakeSingleRunRunner(scenarios_by_sample_index=scenarios)
    execute_multi_run_batch(artifacts.batch_dir, runner=runner)
    return artifacts.batch_dir, runner


def _write_manifest(path: Path) -> None:
    entries = [_entry(1, "a"), _entry(2, "b"), _entry(3, "c")]
    path.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )


def _entry(index: int, digest_char: str) -> dict[str, object]:
    sha256 = digest_char * 64
    return {
        "schema_version": SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION,
        "manifest_id": "manifest-scheduler-test",
        "manifest_created_at_utc": "2026-05-30T10:00:00Z",
        "manifest_tool_version": "0.1.0",
        "sample_index": index,
        "sample_id": sha256,
        "case_name": sha256[:16],
        "sha256": sha256,
        "md5": digest_char * 32,
        "size": 100 + index,
        "original_filename": f"sample-{index}.exe",
        "original_suffix": ".exe",
        "normalized_suffix": ".exe",
        "renamed_filename": f"{index:04d}_{sha256[:16]}.exe",
        "sample_source_kind": "local_platform_path",
        "sample_ref": f"C:\\CloudAvSamples\\indexed\\{index:04d}_{sha256[:16]}.exe",
        "duplicate_group_id": f"sha256:{sha256}",
        "duplicate_of_sample_index": None,
        "aliases": [f"sample-{index}.exe"],
        "entry_status": "ready",
        "skip_reason": None,
        "created_at_utc": "2026-05-30T10:01:00Z",
    }


if __name__ == "__main__":
    unittest.main()
