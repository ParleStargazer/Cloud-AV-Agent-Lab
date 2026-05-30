from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cloud_av_agent_lab.orchestration.multi_run import (
    FakeSingleRunRunner,
    MultiRunStateError,
    SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION,
    create_multi_run_batch_plan,
    execute_multi_run_batch,
    load_existing_multi_run_batch,
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
            self.assertTrue(state["cases"][0]["simulated"])
            self.assertEqual(state["cases"][0]["result_source"], "fake_runner")
            self.assertFalse(state["cases"][0]["resume_eligible"])

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
            self.assertEqual(state["final_status"], "stopped_for_environment_failure")
            self.assertTrue(state["unsafe_to_continue"])
            self.assertTrue(state["manual_intervention_required"])
            self.assertEqual(state["cases"][0]["cleanup_status"], "restore_failed")
            self.assertEqual(state["cases"][0]["failure_kind"], "environment_failure")
            self.assertEqual(state["cases"][1]["case_status"], "planned")

    def test_cleanup_restore_failure_stops_batch_as_environment_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, runner = _plan_and_execute(
                tmp,
                scenarios={1: "cleanup_restore_failed", 2: "completed"},
            )

            self.assertEqual([request.sample_index for request in runner.requests], [1])
            state = json.loads((batch_dir / "multi_run_state.json").read_text("utf-8"))
            self.assertEqual(state["batch_state"], "stopped_for_environment_failure")
            self.assertEqual(state["final_status"], "stopped_for_environment_failure")
            self.assertTrue(state["unsafe_to_continue"])
            self.assertTrue(state["manual_intervention_required"])
            self.assertEqual(state["cases"][0]["cleanup_status"], "restore_failed")
            self.assertEqual(state["cases"][0]["failure_kind"], "environment_failure")
            self.assertEqual(state["cases"][1]["case_status"], "planned")

    def test_case_directory_uses_manifest_case_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, runner = _plan_and_execute(
                tmp,
                indexes=(1,),
                case_names={1: "custom-case-name"},
            )

            self.assertEqual(runner.requests[0].case_dir.name, "0001_custom-case-name")
            self.assertTrue((batch_dir / "cases" / "0001_custom-case-name").is_dir())

    def test_scheduler_writes_expected_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(tmp, indexes=(1,))

            events = read_multi_run_events(batch_dir / "multi_run_events.jsonl")
            event_types = [event["type"] for event in events]
            self.assertIn("lightweight_preflight_started", event_types)
            self.assertIn("lightweight_preflight_passed", event_types)
            self.assertIn("case_started", event_types)
            self.assertIn("single_run_started", event_types)
            self.assertIn("single_run_completed", event_types)
            self.assertIn("case_finalized", event_types)
            self.assertIn("aggregate_summary_written", event_types)
            self.assertEqual(event_types[-1], "batch_finished")

    def test_scheduler_writes_aggregate_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(
                tmp,
                scenarios={1: "completed", 2: "summary_missing", 3: "completed"},
                indexes=(1, 2, 3),
            )

            summary_path = batch_dir / "aggregate_summary.json"
            markdown_path = batch_dir / "aggregate_summary.md"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")

            self.assertEqual(
                summary["schema_version"], "multi-run-aggregate-summary.v1"
            )
            self.assertEqual(summary["denominator"]["selected_samples"], 3)
            self.assertEqual(summary["denominator"]["case_failures"], 1)
            self.assertEqual(summary["denominator"]["environment_failures"], 0)
            self.assertEqual(summary["denominator"]["evaluable_cases"], 2)
            self.assertEqual(summary["detection_rate"]["detected_or_blocked"], 2)
            self.assertEqual(summary["detection_rate"]["denominator"], 2)
            self.assertTrue(summary["detection_rate"]["simulated"])
            self.assertEqual(
                summary["detection_rate"]["rate_kind"], "simulated_detection_rate"
            )
            self.assertEqual(summary["verdict_breakdown"]["detected_or_blocked"], 2)
            self.assertEqual(summary["verdict_breakdown"]["not_evaluable"], 1)
            self.assertEqual(summary["readiness_breakdown"]["ok"], 2)
            self.assertEqual(summary["case_errors"][0]["failure_kind"], "case_failure")
            self.assertEqual(
                summary["cases"][0]["paths"]["evidence_bundle"],
                "cases/0001_aaaaaaaaaaaaaaaa/single_run/"
                "case_evidence_0001_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                "aaaaaaaaaaaaaaaa__huorong.zip",
            )
            self.assertNotIn("\\", summary["cases"][0]["paths"]["evidence_bundle"])
            self.assertIn("Multi-Run Aggregate Summary", markdown)
            self.assertIn("Case failures: 1", markdown)

    def test_aggregate_counts_environment_stopped_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(
                tmp,
                scenarios={1: "cleanup_restore_failed", 2: "completed"},
            )

            summary = json.loads(
                (batch_dir / "aggregate_summary.json").read_text(encoding="utf-8")
            )

            self.assertEqual(summary["final_status"], "stopped_for_environment_failure")
            self.assertTrue(summary["denominator"]["environment_stopped"])
            self.assertEqual(summary["denominator"]["environment_failures"], 1)
            self.assertEqual(summary["denominator"]["case_failures"], 0)
            self.assertEqual(summary["denominator"]["evaluable_cases"], 0)
            self.assertEqual(summary["detection_rate"]["denominator"], 0)

    def test_resume_skips_completed_restored_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(tmp, indexes=(1, 2))
            _mark_cases_as_real_resume_eligible(batch_dir)
            resume_runner = FakeSingleRunRunner()

            state = execute_multi_run_batch(
                batch_dir,
                runner=resume_runner,
                execution_mode="resume",
            )

            self.assertEqual(resume_runner.requests, [])
            self.assertEqual(state.batch_state, "completed")
            events = read_multi_run_events(batch_dir / "multi_run_events.jsonl")
            self.assertIn(
                "case_skipped_by_execution_mode", [event["type"] for event in events]
            )

    def test_resume_runs_completed_case_with_cleanup_unknown_when_state_safe(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(tmp, indexes=(1, 2))
            _mark_cases_as_real_resume_eligible(batch_dir)
            state_path = batch_dir / "multi_run_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["cases"][0]["cleanup_status"] = "unknown"
            state["cases"][0]["resume_eligible"] = False
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            resume_runner = FakeSingleRunRunner()

            execute_multi_run_batch(
                batch_dir,
                runner=resume_runner,
                execution_mode="resume",
            )

            self.assertEqual(
                [request.sample_index for request in resume_runner.requests], [1]
            )
            self.assertEqual(resume_runner.requests[0].attempt, 2)

    def test_resume_rejects_unsafe_existing_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(
                tmp,
                scenarios={1: "environment_failed", 2: "completed"},
            )

            with self.assertRaisesRegex(MultiRunStateError, "unsafe to continue"):
                execute_multi_run_batch(batch_dir, execution_mode="resume")

    def test_rerun_failed_runs_only_case_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(
                tmp,
                scenarios={1: "case_failed", 2: "completed"},
            )
            rerun_runner = FakeSingleRunRunner()

            execute_multi_run_batch(
                batch_dir,
                runner=rerun_runner,
                execution_mode="rerun_failed",
            )

            self.assertEqual(
                [request.sample_index for request in rerun_runner.requests], [1]
            )
            self.assertEqual(rerun_runner.requests[0].attempt, 2)
            self.assertEqual(
                rerun_runner.requests[0].case_dir.as_posix(),
                (
                    batch_dir
                    / "cases"
                    / ("0001_" + "a" * 16)
                    / "attempts"
                    / "attempt_002"
                ).as_posix(),
            )

    def test_rerun_failed_does_not_rerun_environment_failure_with_failed_status(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(
                tmp,
                scenarios={1: "case_failed", 2: "completed"},
            )
            state_path = batch_dir / "multi_run_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["cases"][0]["case_status"] = "failed"
            state["cases"][0]["failure_kind"] = "environment_failure"
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            rerun_runner = FakeSingleRunRunner()

            execute_multi_run_batch(
                batch_dir,
                runner=rerun_runner,
                execution_mode="rerun_failed",
            )

            self.assertEqual(rerun_runner.requests, [])

    def test_force_rerun_runs_all_selected_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(tmp, indexes=(1, 2))
            force_runner = FakeSingleRunRunner()

            execute_multi_run_batch(
                batch_dir,
                runner=force_runner,
                execution_mode="force_rerun",
            )

            self.assertEqual(
                [request.sample_index for request in force_runner.requests], [1, 2]
            )
            self.assertEqual(
                [request.attempt for request in force_runner.requests], [2, 2]
            )

    def test_existing_batch_rejects_manifest_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_dir, _runner = _plan_and_execute(tmp)
            manifest_path = tmp_path / "different_manifest.jsonl"
            _write_manifest(manifest_path, case_names={1: "different-case"})
            manifest = load_sample_manifest(manifest_path)
            selection = parse_sample_selection(manifest.indexes, all_samples=True)

            with self.assertRaisesRegex(MultiRunStateError, "manifest sha256"):
                load_existing_multi_run_batch(
                    batch_root=batch_dir.parent,
                    batch_id=batch_dir.name,
                    product_id="huorong",
                    instance_id="lhins-test",
                    snapshot_id="lhsnap-test",
                    region="ap-singapore",
                    manifest=manifest,
                    selection=selection,
                )

    def test_existing_batch_rejects_product_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(tmp)
            manifest = load_sample_manifest(batch_dir / "sample_manifest.jsonl")
            selection = parse_sample_selection(manifest.indexes, all_samples=True)

            with self.assertRaisesRegex(MultiRunStateError, "product_id"):
                load_existing_multi_run_batch(
                    batch_root=batch_dir.parent,
                    batch_id=batch_dir.name,
                    product_id="windows-defender",
                    instance_id="lhins-test",
                    snapshot_id="lhsnap-test",
                    region="ap-singapore",
                    manifest=manifest,
                    selection=selection,
                )

    def test_existing_batch_rejects_batch_plan_sha256_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir, _runner = _plan_and_execute(tmp)
            plan_path = batch_dir / "batch_plan.json"
            plan_path.write_text(
                plan_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            manifest = load_sample_manifest(batch_dir / "sample_manifest.jsonl")
            selection = parse_sample_selection(manifest.indexes, all_samples=True)

            with self.assertRaisesRegex(MultiRunStateError, "batch plan sha256"):
                load_existing_multi_run_batch(
                    batch_root=batch_dir.parent,
                    batch_id=batch_dir.name,
                    product_id="huorong",
                    instance_id="lhins-test",
                    snapshot_id="lhsnap-test",
                    region="ap-singapore",
                    manifest=manifest,
                    selection=selection,
                )


def _plan_and_execute(
    tmp: str,
    *,
    indexes: tuple[int, ...] = (1, 2),
    scenarios: dict[int, str] | None = None,
    failure_policy: str = "continue",
    case_names: dict[int, str] | None = None,
) -> tuple[Path, FakeSingleRunRunner]:
    tmp_path = Path(tmp)
    manifest_path = tmp_path / "sample_manifest.jsonl"
    _write_manifest(manifest_path, case_names=case_names)
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


def _mark_cases_as_real_resume_eligible(batch_dir: Path) -> None:
    state_path = batch_dir / "multi_run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for case in state["cases"]:
        case["result_source"] = "single_run_runner"
        case["simulated"] = False
        case["resume_eligible"] = True
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _write_manifest(path: Path, *, case_names: dict[int, str] | None = None) -> None:
    entries = [
        _entry(1, "a", case_name=case_names.get(1) if case_names else None),
        _entry(2, "b", case_name=case_names.get(2) if case_names else None),
        _entry(3, "c", case_name=case_names.get(3) if case_names else None),
    ]
    path.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )


def _entry(
    index: int,
    digest_char: str,
    *,
    case_name: str | None = None,
) -> dict[str, object]:
    sha256 = digest_char * 64
    return {
        "schema_version": SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION,
        "manifest_id": "manifest-scheduler-test",
        "manifest_created_at_utc": "2026-05-30T10:00:00Z",
        "manifest_tool_version": "0.1.0",
        "sample_index": index,
        "sample_id": sha256,
        "case_name": case_name or sha256[:16],
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
