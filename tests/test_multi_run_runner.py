from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cloud_av_agent_lab.orchestration.multi_run import (
    FakeSingleRunRunner,
    RealSingleRunRunner,
    SingleRunRequest,
    SingleRunRunnerResult,
    classify_runner_result,
    fake_single_run_result,
)
from cloud_av_agent_lab.orchestration.single_run import SingleRunResult


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
        self.assertEqual(payload["guest_agent_url"], "http://127.0.0.1:8080")
        self.assertEqual(payload["desktop_worker_url"], "http://127.0.0.1:8001")
        self.assertEqual(payload["manifest_sha256"], "c" * 64)
        self.assertEqual(payload["batch_plan_sha256"], "d" * 64)
        self.assertEqual(payload["sha256"], "a" * 64)
        self.assertFalse(payload["defer_final_cleanup"])
        self.assertFalse(payload["skip_initial_restore"])
        self.assertEqual(payload["environment_reused_from_case_id"], "")
        self.assertEqual(payload["settling_cooldown_seconds"], 15.0)
        self.assertEqual(payload["upload_status_timeout_seconds"], 30.0)
        self.assertEqual(payload["post_execution_collection_delay_seconds"], 45.0)
        self.assertNotIn("sample_bytes", payload)
        self.assertNotIn("content", payload)

    def test_real_runner_passes_manifest_sample_ref_to_single_run_options(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sample_path = tmp_path / "indexed" / "0001_sample.exe"
            sample_path.parent.mkdir()
            sample_path.write_bytes(b"harmless")
            request = _request_for_sample(sample_path, tmp_path / "case")
            captured = {}

            def fake_run_single_case(options: object) -> SingleRunResult:
                captured["sample_path"] = str(getattr(options, "sample_path"))
                captured["product_id"] = getattr(options, "product_id")
                captured["guest_agent_url"] = getattr(options, "guest_agent_url")
                captured["desktop_worker_url"] = getattr(options, "desktop_worker_url")
                captured["defer_final_cleanup"] = getattr(
                    options, "defer_final_cleanup"
                )
                captured["skip_initial_restore"] = getattr(
                    options, "skip_initial_restore"
                )
                captured["settling_cooldown_seconds"] = getattr(
                    options, "settling_cooldown_seconds"
                )
                captured["upload_poll_timeout_seconds"] = getattr(
                    options, "upload_poll_timeout_seconds"
                )
                captured["post_execution_collection_delay_seconds"] = getattr(
                    options, "post_execution_collection_delay_seconds"
                )
                run_dir = Path(getattr(options, "runs_dir")) / "run-001"
                run_dir.mkdir(parents=True)
                run_state_path = run_dir / "run_state.json"
                summary_path = run_dir / "case_summary.json"
                evidence_path = run_dir / "case_evidence.zip"
                run_state_path.write_text(
                    json.dumps(
                        {
                            "security_product_readiness": {"status": "ok"},
                            "evidence_export_status": "saved",
                            "warnings": [],
                            "errors": [],
                            "fatal_errors": [],
                            "stages": {"summary": {"path": str(summary_path)}},
                        }
                    ),
                    encoding="utf-8",
                )
                summary_path.write_text("{}", encoding="utf-8")
                evidence_path.write_bytes(b"zip")
                return SingleRunResult(
                    run_id="real-run-001",
                    case_id="real-case-001",
                    run_dir=run_dir,
                    run_state_path=run_state_path,
                    generated_config_path=run_dir / "lab.generated.toml",
                    summary_path=summary_path,
                    evidence_bundle_path=evidence_path,
                    verdict="detected_or_blocked",
                    confidence="high",
                    final_status="completed",
                    cleanup_status="restored",
                    emergency_poweroff_status="not_needed",
                )

            result = RealSingleRunRunner(run_single_case_func=fake_run_single_case).run(
                request
            )

            self.assertEqual(captured["sample_path"], str(sample_path))
            self.assertEqual(captured["product_id"], "huorong")
            self.assertEqual(captured["guest_agent_url"], "http://127.0.0.1:8080")
            self.assertEqual(captured["desktop_worker_url"], "http://127.0.0.1:8001")
            self.assertFalse(captured["defer_final_cleanup"])
            self.assertFalse(captured["skip_initial_restore"])
            self.assertEqual(captured["settling_cooldown_seconds"], 15.0)
            self.assertEqual(captured["upload_poll_timeout_seconds"], 30.0)
            self.assertEqual(captured["post_execution_collection_delay_seconds"], 45.0)
            self.assertEqual(result.run_id, "real-run-001")
            self.assertEqual(result.case_id, "real-case-001")
            self.assertEqual(result.result_source, "single_run_runner")
            self.assertFalse(result.simulated)
            self.assertEqual(result.evidence_status, "exported")
            self.assertEqual(result.summary_status, "collected")
            self.assertEqual(result.readiness_status, "ok")
            self.assertEqual(result.run_state_path, "case/run-001/run_state.json")
            self.assertEqual(result.case_summary_path, "case/run-001/case_summary.json")
            self.assertEqual(
                result.evidence_bundle_path, "case/run-001/case_evidence.zip"
            )
            self.assertFalse(Path(result.run_state_path).is_absolute())

    def test_real_runner_rejects_sample_ref_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sample_path = tmp_path / "indexed" / "0001_sample.exe"
            sample_path.parent.mkdir()
            sample_path.write_bytes(b"changed")

            result = RealSingleRunRunner(
                run_single_case_func=lambda _options: self.fail("should not run")
            ).run(_request_for_sample(sample_path, tmp_path / "case", sha256="a" * 64))

            self.assertEqual(result.case_status, "failed")
            self.assertEqual(result.failure_kind, "case_failure")
            self.assertIn("metadata does not match", result.error_summary)

    def test_real_runner_rejects_non_indexed_sample_ref_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sample_path = tmp_path / "raw" / "sample.exe"
            sample_path.parent.mkdir()
            sample_path.write_bytes(b"harmless")

            result = RealSingleRunRunner(
                run_single_case_func=lambda _options: self.fail("should not run")
            ).run(_request_for_sample(sample_path, tmp_path / "case"))

            self.assertEqual(result.case_status, "failed")
            self.assertEqual(result.failure_kind, "case_failure")
            self.assertIn("indexed sample mirror", result.error_summary)

    def test_real_runner_maps_cleanup_restore_failed_to_environment_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sample_path = tmp_path / "indexed" / "0001_sample.exe"
            sample_path.parent.mkdir()
            sample_path.write_bytes(b"harmless")
            request = _request_for_sample(sample_path, tmp_path / "case")

            def fake_run_single_case(options: object) -> SingleRunResult:
                run_dir = Path(getattr(options, "runs_dir")) / "run-001"
                run_dir.mkdir(parents=True)
                run_state_path = run_dir / "run_state.json"
                run_state_path.write_text(
                    json.dumps(
                        {
                            "warnings": [{"message": "cleanup warning"}],
                            "errors": [{"message": "cleanup failed"}],
                            "fatal_errors": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return SingleRunResult(
                    run_id="real-run-001",
                    case_id="real-case-001",
                    run_dir=run_dir,
                    run_state_path=run_state_path,
                    generated_config_path=run_dir / "lab.generated.toml",
                    summary_path=None,
                    evidence_bundle_path=None,
                    verdict="inconclusive",
                    confidence="",
                    final_status="failed_cleanup_failed",
                    cleanup_status="restore_failed",
                    emergency_poweroff_status="failed",
                )

            result = RealSingleRunRunner(run_single_case_func=fake_run_single_case).run(
                request
            )

            self.assertEqual(result.failure_kind, "environment_failure")
            self.assertEqual(result.case_status, "stopped_environment_failure")
            self.assertTrue(result.unsafe_to_continue)
            self.assertTrue(result.manual_intervention_required)
            self.assertEqual(result.error_summary, "cleanup failed")


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
        guest_agent_url="http://127.0.0.1:8080",
        desktop_worker_url="http://127.0.0.1:8001",
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


def _request_for_sample(
    sample_path: Path,
    case_dir: Path,
    *,
    sha256: str | None = None,
) -> SingleRunRequest:
    import hashlib

    content = sample_path.read_bytes()
    return SingleRunRequest(
        batch_id="batch-001",
        sample_index=1,
        sample_id=hashlib.sha256(content).hexdigest(),
        case_name="sample",
        case_id="0001_sample__huorong",
        run_id="run-001",
        product_id="huorong",
        instance_id="lhins-test",
        snapshot_id="lhsnap-test",
        region="ap-singapore",
        guest_agent_url="http://127.0.0.1:8080",
        desktop_worker_url="http://127.0.0.1:8001",
        sample_ref=str(sample_path),
        manifest_sha256="c" * 64,
        batch_plan_sha256="d" * 64,
        sha256=sha256 or hashlib.sha256(content).hexdigest(),
        md5=hashlib.md5(content, usedforsecurity=False).hexdigest(),
        size=len(content),
        original_filename=sample_path.name,
        case_dir=case_dir,
        dry_run=False,
    )


if __name__ == "__main__":
    unittest.main()
