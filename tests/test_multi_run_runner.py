from __future__ import annotations

import json
import unittest
from pathlib import Path

from cloud_av_agent_lab.orchestration.multi_run import (
    FakeSingleRunRunner,
    SingleRunRequest,
    SingleRunRunnerResult,
    classify_runner_result,
    fake_single_run_result,
)


class MultiRunFakeRunnerTests(unittest.TestCase):
    def test_completed_fake_result_maps_to_simulated_non_resumable_case_state(
        self,
    ) -> None:
        request = _request()

        result = fake_single_run_result("completed", request)
        case_state = result.to_case_state(request)

        self.assertEqual(result.case_status, "completed")
        self.assertEqual(result.cleanup_status, "restored")
        self.assertEqual(result.result_source, "fake_runner")
        self.assertTrue(result.simulated)
        self.assertFalse(case_state.resume_eligible)
        self.assertEqual(case_state.result_source, "fake_runner")
        self.assertTrue(case_state.simulated)
        self.assertEqual(case_state.case_name, "eicar")
        self.assertEqual(case_state.evidence_status, "exported")
        json.dumps(result.to_dict())
        json.dumps(case_state.to_dict())

    def test_real_completed_result_maps_to_resumable_case_state(self) -> None:
        request = _request()
        result = SingleRunRunnerResult(
            run_id=request.run_id,
            case_id=request.case_id,
            final_status="completed",
            case_status="completed",
            single_run_status="completed",
            cleanup_status="restored",
            evidence_status="exported",
            summary_status="collected",
            readiness_status="ok",
            verdict="detected_or_blocked",
            confidence="high",
            result_source="single_run_runner",
            simulated=False,
        )

        case_state = result.to_case_state(request)

        self.assertTrue(case_state.resume_eligible)
        self.assertEqual(case_state.failure_kind, None)
        self.assertFalse(case_state.simulated)

    def test_fake_runner_records_requests_and_uses_per_index_scenario(self) -> None:
        runner = FakeSingleRunRunner(
            default_scenario="completed",
            scenarios_by_sample_index={2: "case_failed"},
        )

        first = runner.run(_request(sample_index=1))
        second = runner.run(_request(sample_index=2))

        self.assertEqual(len(runner.requests), 2)
        self.assertEqual(first.case_status, "completed")
        self.assertEqual(second.case_status, "failed")
        self.assertEqual(second.failure_kind, "case_failure")

    def test_fake_runner_covers_environment_failure_fixture(self) -> None:
        result = fake_single_run_result("environment_failed", _request())

        self.assertEqual(result.case_status, "stopped_environment_failure")
        self.assertEqual(result.failure_kind, "environment_failure")
        self.assertTrue(result.unsafe_to_continue)
        self.assertTrue(result.manual_intervention_required)

    def test_fake_runner_covers_timeout_fixture(self) -> None:
        result = fake_single_run_result("timeout", _request())

        self.assertEqual(result.single_run_status, "timeout")
        self.assertEqual(result.failure_kind, "case_failure")
        self.assertEqual(result.evidence_status, "partial")

    def test_fake_runner_covers_summary_missing_fixture(self) -> None:
        result = fake_single_run_result("summary_missing", _request())

        self.assertEqual(result.summary_status, "missing")
        self.assertEqual(result.failure_kind, "case_failure")
        self.assertIn("case_summary.json missing", result.warnings)

    def test_fake_runner_covers_cleanup_unknown_fixture(self) -> None:
        result = fake_single_run_result("cleanup_unknown", _request())

        self.assertEqual(result.cleanup_status, "unknown")
        self.assertEqual(result.failure_kind, "environment_failure")
        self.assertTrue(result.manual_intervention_required)

    def test_fake_runner_covers_cleanup_restore_failed_fixture(self) -> None:
        result = fake_single_run_result("cleanup_restore_failed", _request())

        self.assertEqual(result.cleanup_status, "restore_failed")
        self.assertEqual(result.failure_kind, "environment_failure")
        self.assertTrue(result.unsafe_to_continue)

    def test_failure_classifier_infers_environment_failure_from_cleanup(self) -> None:
        request = _request()
        result = SingleRunRunnerResult(
            run_id=request.run_id,
            case_id=request.case_id,
            final_status="completed_with_cleanup_warning",
            case_status="completed",
            single_run_status="completed",
            cleanup_status="unknown",
            evidence_status="exported",
            summary_status="collected",
            result_source="single_run_runner",
            simulated=False,
        )

        case_state = result.to_case_state(request)

        self.assertEqual(classify_runner_result(result), "environment_failure")
        self.assertEqual(result.to_dict()["failure_kind"], "environment_failure")
        self.assertEqual(case_state.failure_kind, "environment_failure")
        self.assertFalse(case_state.resume_eligible)

    def test_request_serialization_contains_metadata_not_sample_bytes(self) -> None:
        payload = _request().to_dict()

        self.assertEqual(payload["sample_ref"], r"C:\CloudAvSamples\eicar.bat")
        self.assertEqual(payload["manifest_sha256"], "c" * 64)
        self.assertEqual(payload["batch_plan_sha256"], "d" * 64)
        self.assertEqual(payload["sha256"], "a" * 64)
        self.assertNotIn("sample_bytes", payload)
        self.assertNotIn("content", payload)


def _request(sample_index: int = 1) -> SingleRunRequest:
    return SingleRunRequest(
        batch_id="batch-001",
        sample_index=sample_index,
        sample_id=f"sample-{sample_index:03d}",
        case_name="eicar",
        case_id=f"{sample_index:04d}_sample-{sample_index:03d}__huorong",
        run_id=f"run-{sample_index:03d}",
        product_id="huorong",
        instance_id="lhins-test",
        snapshot_id="lhsnap-test",
        region="ap-singapore",
        sample_ref=r"C:\CloudAvSamples\eicar.bat",
        manifest_sha256="c" * 64,
        batch_plan_sha256="d" * 64,
        sha256="a" * 64,
        md5="b" * 32,
        size=68,
        original_filename="eicar.bat",
        case_dir=Path(f"cases/{sample_index:04d}_sample"),
        dry_run=True,
    )


if __name__ == "__main__":
    unittest.main()
