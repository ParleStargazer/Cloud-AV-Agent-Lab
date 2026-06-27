from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.adapters.cloud import VMOperationResponse
from cloud_av_agent_lab.adapters.guest_agent_client import (
    GuestAgentError,
    GuestAgentResponse,
)
from cloud_av_agent_lab.cli import main
from cloud_av_agent_lab.orchestration.locks import (
    InstanceLockedError,
    _write_json_atomic,
    acquire_lock,
    lock_file_for,
)
from cloud_av_agent_lab.orchestration.single_run import (
    SingleRunOptions,
    SingleRunResult,
    _check_security_product_readiness_warning_only,
    run_single_case,
)
from cloud_av_agent_lab.orchestration.run_state import RunState
from cloud_av_agent_lab.orchestration.timeout import NetworkTimeoutProfile


class SingleRunTests(TestCase):
    def test_single_run_generates_artifacts_and_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient()
            adapter = FakeCloudAdapter()

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: adapter,
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.verdict, "detected_or_blocked")
            self.assertTrue(result.generated_config_path.is_file())
            self.assertTrue(result.run_state_path.is_file())
            self.assertTrue((result.run_dir / "run.log").is_file())
            self.assertTrue((result.run_dir / "case_summary.json").is_file())
            self.assertTrue((result.run_dir / "case_summary.md").is_file())
            self.assertTrue(result.evidence_bundle_path)
            self.assertTrue(result.evidence_bundle_path.is_file())
            self.assertFalse(
                lock_file_for(root / "runs" / ".locks", "lhins-example").exists()
            )
            self.assertGreaterEqual(adapter.calls.count("restore_snapshot"), 2)
            self.assertEqual(client.health_calls, 2)
            self.assertEqual(client.execute_dry_runs, [False])
            self.assertEqual(
                client.calls[:3],
                ["prepare_case", "check_security_product_readiness", "upload_sample"],
            )
            generated_config = result.generated_config_path.read_text(encoding="utf-8")
            self.assertIn('mode = "real"', generated_config)
            self.assertIn("dry_run = false", generated_config)
            self.assertIn("[guest_agent.execution]", generated_config)
            self.assertIn("enabled = true", generated_config)
            self.assertIn("[guest_agent.desktop_worker]", generated_config)
            self.assertIn('base_url = "http://127.0.0.1:8001"', generated_config)
            self.assertIn(
                f'md5 = "{hashlib.md5(b"harmless placeholder").hexdigest()}"',
                generated_config,
            )
            self.assertNotIn("agent-secret", generated_config)

            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["final_status"], "completed")
            self.assertEqual(run_state["status"], "completed")
            self.assertEqual(run_state["selected_product_id"], "huorong")
            self.assertEqual(
                run_state["sample"]["md5"],
                hashlib.md5(b"harmless placeholder").hexdigest(),
            )
            self.assertEqual(run_state["test_verdict"], "detected_or_blocked")
            self.assertEqual(run_state["evidence_export_status"], "saved")
            self.assertEqual(run_state["cleanup_status"], "dry_run")
            self.assertTrue(run_state["desktop_worker_ready"])
            self.assertEqual(run_state["desktop_session_state"], "active")
            self.assertEqual(
                run_state["stages"]["environment"]["desktop_session_state"],
                "active",
            )
            self.assertEqual(run_state["stages"]["delivery"]["upload_state"], "stable")
            self.assertEqual(
                run_state["stages"]["security_product_readiness"]["status"],
                "ok",
            )
            self.assertEqual(
                run_state["stages"]["security_product_readiness"]["state"],
                "ready",
            )
            self.assertEqual(
                run_state["security_product_readiness"]["state"],
                "ready",
            )
            self.assertTrue(
                run_state["stages"]["environment"]["product_readiness_checked"]
            )
            self.assertTrue(
                run_state["stages"]["environment"]["product_environment_ready"]
            )
            self.assertTrue(run_state["environment"]["product_readiness_checked"])
            self.assertTrue(run_state["environment"]["product_environment_ready"])
            readiness_artifact = result.run_dir / "case_security_product_readiness.json"
            self.assertTrue(readiness_artifact.is_file())
            readiness_payload = json.loads(
                readiness_artifact.read_text(encoding="utf-8")
            )
            self.assertEqual(readiness_payload["state"], "ready")
            self.assertEqual(
                run_state["artifacts"]["security_product_readiness"],
                str(readiness_artifact),
            )
            self.assertEqual(
                run_state["stages"]["execution"]["action_status"],
                "observed",
            )
            self.assertEqual(run_state["stages"]["evidence"]["status"], "saved")
            self.assertIn("evidence_bundle", run_state["artifacts"])
            self.assertEqual(
                client.upload_md5s,
                [hashlib.md5(b"harmless placeholder").hexdigest()],
            )

    def test_single_run_can_defer_final_cleanup_after_evidence_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient()
            adapter = FakeCloudAdapter()

            result = run_single_case(
                _options(root, sample_path, defer_final_cleanup=True),
                cloud_adapter_factory=lambda *args, **kwargs: adapter,
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.cleanup_status, "deferred_to_next_case")
            self.assertEqual(adapter.calls.count("restore_snapshot"), 1)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["cleanup_status"], "deferred_to_next_case")
            self.assertEqual(
                run_state["stages"]["cleanup"]["deferred_reason"],
                "next_case_initial_restore_required",
            )

    def test_single_run_can_skip_initial_restore_for_fastmode_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient()
            adapter = FakeCloudAdapter()

            result = run_single_case(
                _options(root, sample_path, skip_initial_restore=True),
                cloud_adapter_factory=lambda *args, **kwargs: adapter,
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(adapter.calls.count("restore_snapshot"), 1)
            self.assertEqual(client.health_calls, 1)
            self.assertEqual(client.worker_status_calls, 1)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                run_state["initial_restore_status"],
                "skipped_fastmode_reuse",
            )
            self.assertEqual(
                run_state["stages"]["environment"]["initial_restore"],
                "skipped_fastmode_reuse",
            )
            self.assertEqual(
                run_state["stages"]["environment"]["ready_gate_mode"],
                "fastmode_quick",
            )
            self.assertEqual(
                run_state["stages"]["environment"]["guest_ready_successes_required"],
                1,
            )
            self.assertEqual(
                run_state["stages"]["environment"]["settling_cooldown_seconds"],
                3.0,
            )

    def test_execution_status_failure_continues_to_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient(
                execution_status_error=GuestAgentError(
                    "Desktop Worker execution-status request failed: TimeoutError",
                    status_code=502,
                    source="network",
                )
            )
            adapter = FakeCloudAdapter()

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: adapter,
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertEqual(client.collection_products, ["huorong"])
            self.assertTrue(result.evidence_bundle_path)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                run_state["stages"]["execution"]["action_status"],
                "observation_unavailable",
            )
            self.assertEqual(
                run_state["stages"]["execution"]["state"],
                "execution_observation_unavailable",
            )
            self.assertEqual(
                run_state["stages"]["execution"]["observation_exit_reason"],
                "execution_status_unavailable",
            )
            self.assertEqual(run_state["stages"]["collection"]["evidence_count"], 1)
            self.assertEqual(run_state["stages"]["evidence"]["status"], "saved")

    def test_lock_heartbeat_failure_after_collection_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient()
            lock = FakeInstanceLock(fail_on_call=8)

            with patch(
                "cloud_av_agent_lab.orchestration.single_run.acquire_lock",
                return_value=lock,
            ):
                result = run_single_case(
                    _options(root, sample_path, skip_initial_restore=True),
                    cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                    guest_client_factory=lambda config: client,
                    sleep=lambda seconds: None,
                )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertTrue(lock.released)
            self.assertTrue((result.run_dir / "case_summary.json").is_file())
            self.assertTrue(result.evidence_bundle_path)
            self.assertTrue(result.evidence_bundle_path.is_file())
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                run_state["stages"]["case_summary"]["lock_heartbeat_status"],
                "warning",
            )
            self.assertNotIn("fatal_errors", run_state["stages"]["case_summary"])
            self.assertTrue(
                any(
                    warning["stage"] == "case_summary"
                    and "lock heartbeat failed after collection completed"
                    in warning["message"]
                    for warning in run_state["warnings"]
                )
            )

    def test_fastmode_quick_gate_failure_stops_before_prepare_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient(
                worker_ready=False,
                worker_reason="desktop worker unavailable",
            )
            adapter = FakeCloudAdapter()

            result = run_single_case(
                _options(root, sample_path, skip_initial_restore=True),
                cloud_adapter_factory=lambda *args, **kwargs: adapter,
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "failed")
            self.assertEqual(client.calls, [])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                run_state["stages"]["environment"]["ready_gate_mode"],
                "fastmode_quick",
            )
            self.assertEqual(
                run_state["stages"]["environment"]["ready_gate_failure_kind"],
                "quick_gate_failed",
            )
            self.assertFalse(
                run_state["stages"]["environment"]["fallback_restore_attempted"]
            )

    def test_cli_single_run_entry_executes_readiness_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient()
            captured: dict[str, SingleRunResult] = {}

            def run_from_cli(options: SingleRunOptions) -> SingleRunResult:
                result = run_single_case(
                    options,
                    cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                    guest_client_factory=lambda config: client,
                    sleep=lambda seconds: None,
                )
                captured["result"] = result
                return result

            with (
                patch(
                    "cloud_av_agent_lab.cli.run_single_case", side_effect=run_from_cli
                ),
                patch("cloud_av_agent_lab.cli._confirm_single_run_real_operation"),
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "single-run",
                        "--instance-id",
                        "lhins-example",
                        "--snapshot-id",
                        "lhsnap-example",
                        "--region",
                        "ap-singapore",
                        "--sample-name",
                        "eicar",
                        "--sample-path",
                        str(sample_path),
                        "--product",
                        "huorong",
                        "--guest-agent-url",
                        "http://127.0.0.1:8080",
                        "--runs-dir",
                        str(root / "runs"),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                client.calls[:3],
                ["prepare_case", "check_security_product_readiness", "upload_sample"],
            )
            result = captured["result"]
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            readiness = run_state["stages"]["security_product_readiness"]
            self.assertEqual(readiness["status"], "ok")
            self.assertEqual(readiness["state"], "ready")
            self.assertEqual(
                run_state["security_product_readiness"]["state"],
                "ready",
            )
            self.assertTrue(
                (result.run_dir / "case_security_product_readiness.json").is_file()
            )

    def test_product_warmup_runs_after_worker_ready_before_prepare_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient()

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="qihoo-360",
                    product_warmup_enabled=True,
                    product_warmup_cooldown_seconds=4.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(client.warmup_products, ["qihoo-360"])
            self.assertLess(
                client.calls.index("warm_up_security_product"),
                client.calls.index("prepare_case"),
            )
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            warmup = run_state["stages"]["product_warmup"]
            self.assertTrue(warmup["enabled"])
            self.assertEqual(warmup["status"], "ok")
            self.assertEqual(warmup["state"], "started")
            self.assertEqual(warmup["action"], "open_main_ui")
            self.assertEqual(warmup["cooldown_seconds"], 4.0)
            self.assertFalse(warmup["client_supplied_path"])

    def test_fastmode_reuse_skips_product_warmup_cooldown_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient()
            sleep_calls: list[float] = []

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="qihoo-360",
                    product_warmup_enabled=True,
                    product_warmup_cooldown_seconds=8.0,
                    skip_initial_restore=True,
                    post_execution_collection_delay_seconds=0.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleep_calls.append,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(client.warmup_products, ["qihoo-360"])
            self.assertIn(3.0, sleep_calls)
            self.assertNotIn(8.0, sleep_calls)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                run_state["stages"]["environment"]["settling_cooldown_seconds"],
                3.0,
            )
            warmup = run_state["stages"]["product_warmup"]
            self.assertEqual(warmup["status"], "ok")
            self.assertEqual(warmup["configured_cooldown_seconds"], 8.0)
            self.assertEqual(warmup["cooldown_seconds"], 0.0)
            self.assertEqual(
                warmup["cooldown_skipped_reason"],
                "fastmode_environment_reuse",
            )

    def test_single_run_windows_defender_product_flows_through_all_stages(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient()

            result = run_single_case(
                _options(root, sample_path, product_id="windows-defender"),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(client.prepare_products, ["windows-defender"])
            self.assertEqual(client.readiness_products, ["windows-defender"])
            self.assertEqual(client.collection_products, ["windows-defender"])
            generated_config = result.generated_config_path.read_text(encoding="utf-8")
            self.assertIn('id = "windows-defender"', generated_config)
            self.assertIn(
                'log_paths = ["Microsoft-Windows-Windows Defender/Operational"]',
                generated_config,
            )
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["product_id"], "windows-defender")
            self.assertEqual(run_state["selected_product_id"], "windows-defender")
            self.assertEqual(
                run_state["stages"]["security_product_readiness"]["product_id"],
                "windows-defender",
            )

    def test_single_run_qihoo_360_product_flows_through_all_stages(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient()

            result = run_single_case(
                _options(root, sample_path, product_id="qihoo-360"),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(client.prepare_products, ["qihoo-360"])
            self.assertEqual(client.readiness_products, ["qihoo-360"])
            self.assertEqual(client.collection_products, ["qihoo-360"])
            generated_config = result.generated_config_path.read_text(encoding="utf-8")
            self.assertIn('id = "qihoo-360"', generated_config)
            self.assertIn(r"C:\\ProgramData\\360safe\\logs", generated_config)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["product_id"], "qihoo-360")
            self.assertEqual(run_state["selected_product_id"], "qihoo-360")
            self.assertEqual(
                run_state["stages"]["security_product_readiness"]["product_id"],
                "qihoo-360",
            )

    def test_single_run_tencent_pc_manager_product_flows_through_all_stages(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.exe"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            client = FakeGuestClient()

            result = run_single_case(
                _options(root, sample_path, product_id="tencent-pc-manager"),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(client.prepare_products, ["tencent-pc-manager"])
            self.assertEqual(client.readiness_products, ["tencent-pc-manager"])
            self.assertEqual(client.collection_products, ["tencent-pc-manager"])
            generated_config = result.generated_config_path.read_text(encoding="utf-8")
            self.assertIn('id = "tencent-pc-manager"', generated_config)
            self.assertIn(
                r"C:\\ProgramData\\Tencent\\QQPCMgr\\Quarantine",
                generated_config,
            )
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["product_id"], "tencent-pc-manager")
            self.assertEqual(run_state["selected_product_id"], "tencent-pc-manager")
            self.assertEqual(
                run_state["stages"]["security_product_readiness"]["product_id"],
                "tencent-pc-manager",
            )

    def test_single_run_dry_run_generates_mock_config_and_dry_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient()

            result = run_single_case(
                _options(root, sample_path, dry_run=True),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            generated_config = result.generated_config_path.read_text(encoding="utf-8")
            self.assertIn('mode = "mock"', generated_config)
            self.assertIn("dry_run = true", generated_config)
            self.assertIn("enabled = false", generated_config)
            self.assertEqual(client.execute_dry_runs, [True])

    def test_single_run_readiness_warning_states_continue_to_upload(self) -> None:
        for readiness_state in ("partial", "not_ready", "unknown", "unsupported"):
            with self.subTest(readiness_state=readiness_state):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    sample_path = root / "sample.bin"
                    sample_path.write_bytes(b"harmless")
                    client = FakeGuestClient(readiness_state=readiness_state)

                    result = run_single_case(
                        _options(root, sample_path),
                        cloud_adapter_factory=lambda *args, **kwargs: (
                            FakeCloudAdapter()
                        ),
                        guest_client_factory=lambda config: client,
                        sleep=lambda seconds: None,
                    )

                    self.assertEqual(result.final_status, "completed_with_warnings")
                    self.assertIn("upload_sample", client.calls)
                    self.assertLess(
                        client.calls.index("check_security_product_readiness"),
                        client.calls.index("upload_sample"),
                    )
                    run_state = json.loads(
                        result.run_state_path.read_text(encoding="utf-8")
                    )
                    readiness = run_state["stages"]["security_product_readiness"]
                    self.assertEqual(readiness["status"], "warning")
                    self.assertEqual(readiness["state"], readiness_state)
                    self.assertEqual(
                        run_state["security_product_readiness"]["state"],
                        readiness_state,
                    )
                    self.assertTrue(
                        run_state["stages"]["environment"]["product_readiness_checked"]
                    )
                    self.assertFalse(
                        run_state["stages"]["environment"]["product_environment_ready"]
                    )
                    self.assertTrue(
                        run_state["environment"]["product_readiness_checked"]
                    )
                    self.assertFalse(
                        run_state["environment"]["product_environment_ready"]
                    )
                    readiness_payload = json.loads(
                        (
                            result.run_dir / "case_security_product_readiness.json"
                        ).read_text(encoding="utf-8")
                    )
                    self.assertEqual(readiness_payload["state"], readiness_state)
                    self.assertIn(
                        "warning-only",
                        (result.run_dir / "run.log").read_text(encoding="utf-8"),
                    )

    def test_single_run_readiness_api_failure_is_warning_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient(
                readiness_error=GuestAgentError(
                    "Authorization: Bearer should-not-leak token=should-not-leak",
                    source="network",
                )
            )

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertIn("upload_sample", client.calls)
            run_state_text = result.run_state_path.read_text(encoding="utf-8")
            run_log = (result.run_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn("readiness API call failed", run_state_text)
            self.assertIn("readiness is warning-only", run_log)
            self.assertNotIn("should-not-leak", run_state_text)
            self.assertNotIn("should-not-leak", run_log)
            run_state = json.loads(run_state_text)
            readiness = run_state["stages"]["security_product_readiness"]
            self.assertEqual(readiness["status"], "warning")
            self.assertEqual(readiness["state"], "unknown")
            self.assertFalse(
                run_state["stages"]["environment"]["product_readiness_checked"]
            )
            self.assertIsNone(
                run_state["stages"]["environment"]["product_environment_ready"]
            )
            self.assertFalse(run_state["environment"]["product_readiness_checked"])
            self.assertIsNone(run_state["environment"]["product_environment_ready"])
            readiness_payload = json.loads(
                (result.run_dir / "case_security_product_readiness.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(readiness_payload["status"], "warning")
            self.assertEqual(readiness_payload["state"], "unknown")

    def test_readiness_warning_only_skips_when_product_id_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = RunState(
                Path(tmp) / "run_state.json",
                run_id="run-001",
                case_id="case-001",
                instance_id="lhins-example",
                snapshot_id="lhsnap-example",
                region="ap-singapore",
                product_id="",
                sample_name="eicar",
                sample_path="eicar.txt",
            )

            result = _check_security_product_readiness_warning_only(
                client=FakeGuestClient(),
                state=state,
                case_id="case-001",
                product_id="",
            )

            self.assertEqual(result["status"], "skipped")
            run_state = json.loads(state.path.read_text(encoding="utf-8"))
            readiness = run_state["stages"]["security_product_readiness"]
            self.assertEqual(readiness["status"], "skipped")
            self.assertEqual(readiness["reason"], "product_id is not configured")
            self.assertFalse(
                run_state["stages"]["environment"]["product_readiness_checked"]
            )
            self.assertIsNone(
                run_state["stages"]["environment"]["product_environment_ready"]
            )
            self.assertFalse(run_state["environment"]["product_readiness_checked"])
            self.assertIsNone(run_state["environment"]["product_environment_ready"])
            readiness_payload = json.loads(
                (state.path.parent / "case_security_product_readiness.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(readiness_payload["status"], "skipped")

    def test_single_run_fails_before_delivery_when_worker_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient(
                worker_ready=False,
                worker_reason="desktop session state is not active",
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    guest_ready_timeout_seconds=0.01,
                    guest_ready_successes=1,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "failed")
            self.assertEqual(client.execute_dry_runs, [])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertFalse(run_state.get("case_started", False))
            self.assertIn("desktop session", run_state["errors"][0]["message"])

    def test_collection_remote_failure_continues_to_summary_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient(fail_collect=True)

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    salvage_timeout=NetworkTimeoutProfile(
                        connect_seconds=2,
                        read_seconds=5,
                    ),
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertTrue(result.evidence_bundle_path)
            self.assertTrue(result.evidence_bundle_path.is_file())
            self.assertEqual(client.export_timeouts[-1], 120)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["evidence_export_status"], "saved")
            self.assertTrue(run_state["case_started"])
            self.assertEqual(run_state["stages"]["collection"]["state"], "failed")
            self.assertEqual(
                run_state["stages"]["security_product_readiness"]["status"],
                "ok",
            )
            self.assertEqual(
                run_state["stages"]["security_product_readiness"]["state"],
                "ready",
            )
            self.assertTrue(run_state["warnings"])

    def test_removed_after_save_skips_execution_but_continues_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient(status_upload_state="removed_after_save")

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertEqual(client.execute_dry_runs, [])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["post_upload_state"], "removed_after_save")
            self.assertEqual(run_state["execution_action_status"], "skipped")
            self.assertEqual(
                run_state["execution_action_state"],
                "skipped_removed_after_save",
            )
            self.assertEqual(run_state["evidence_export_status"], "saved")
            self.assertEqual(
                run_state["stages"]["execution"]["state"],
                "skipped_removed_after_save",
            )

    def test_locked_or_busy_skips_execution_but_continues_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient(status_upload_state="locked_or_busy")

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertEqual(client.execute_dry_runs, [])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["post_upload_state"], "locked_or_busy")
            self.assertEqual(run_state["execution_action_status"], "skipped")
            self.assertEqual(
                run_state["execution_action_state"],
                "skipped_locked_or_busy",
            )

    def test_upload_status_polling_starts_immediately_without_fixed_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"harmless")
            sleeps: list[float] = []
            client = FakeGuestClient(status_upload_state="stable")

            result = run_single_case(
                _options(root, sample_path, upload_poll_timeout_seconds=0.0),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertNotIn(10.0, sleeps)
            self.assertGreaterEqual(client.case_status_calls, 1)
            run_log = (result.run_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn(
                "upload saved; polling post-upload state immediately",
                run_log,
            )
            self.assertNotIn("upload saved; waiting 10s", run_log)

    def test_upload_status_network_error_reconnects_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"harmless")
            sleeps: list[float] = []
            client = FakeGuestClient(
                status_upload_state="stable",
                case_status_errors=[
                    GuestAgentError(
                        "network unavailable",
                        source="network",
                    )
                ],
            )

            result = run_single_case(
                _options(root, sample_path, upload_poll_timeout_seconds=2.0),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertGreaterEqual(client.health_calls, 4)
            self.assertGreater(client.case_status_calls, 1)
            self.assertIn(1.0, sleeps)
            run_log = (result.run_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn("Guest Agent reconnect health ok 2/2", run_log)
            self.assertIn(
                "Guest Agent reconnected; retrying upload status query",
                run_log,
            )

    def test_nonfatal_execute_error_continues_to_collection_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient(
                execute_error=GuestAgentError(
                    "uploaded sample is missing before execution",
                    status_code=400,
                    source="remote",
                )
            )

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["post_upload_state"], "stable")
            self.assertEqual(run_state["execution_action_status"], "not_started")
            self.assertEqual(
                run_state["execution_action_state"],
                "sample_missing_before_execution",
            )
            self.assertEqual(run_state["stages"]["execution"]["error_source"], "remote")
            self.assertEqual(run_state["stages"]["execution"]["error_status_code"], 400)
            self.assertEqual(run_state["evidence_export_status"], "saved")

    def test_worker_busy_execute_error_is_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient(
                execute_error=GuestAgentError(
                    "desktop worker is busy",
                    status_code=409,
                    source="remote",
                )
            )

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["execution_action_state"], "worker_busy")
            self.assertEqual(run_state["evidence_export_status"], "saved")

    def test_batch_script_uses_batch_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            client = FakeGuestClient(status_upload_state="stable")

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(client.execute_handler_ids, ["batch_script"])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                run_state["stages"]["execution"]["handler_id"],
                "batch_script",
            )
            self.assertEqual(
                run_state["stages"]["execution"]["execution_mode"],
                "script_via_cmd",
            )

    def test_single_run_waits_after_execution_exit_before_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            sleeps: list[float] = []

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    post_execution_collection_delay_seconds=45.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: FakeGuestClient(),
                sleep=sleeps.append,
            )

            self.assertIn(45.0, sleeps)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            execution = run_state["stages"]["execution"]
            self.assertTrue(execution["execution_terminal"])
            self.assertEqual(execution["children_count"], 0)
            self.assertEqual(execution["observation_exit_reason"], "terminal_state")
            self.assertEqual(execution["product_probe_count"], 0)
            self.assertEqual(execution["product_probe_last_state"], "")
            self.assertEqual(
                run_state["stages"]["collection"][
                    "post_execution_collection_delay_seconds"
                ],
                45.0,
            )
            run_log = (result.run_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn(
                "post-execution collection delay started after execution exit: 45s",
                run_log,
            )

    def test_product_probe_can_end_post_execution_delay_early(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            sleeps: list[float] = []
            client = FakeGuestClient(probe_states=["strong_signal_observed"])

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="tencent-pc-manager",
                    post_execution_collection_delay_seconds=45.0,
                    product_probe_enabled=True,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertIn(1.0, sleeps)
            self.assertNotIn(45.0, sleeps)
            self.assertEqual(client.probe_products, ["tencent-pc-manager"])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            collection = run_state["stages"]["collection"]
            self.assertTrue(collection["product_probe_enabled"])
            self.assertEqual(
                collection["product_probe_exit_reason"],
                "strong_signal_observed",
            )
            self.assertEqual(collection["product_probe_count"], 1)
            self.assertEqual(
                collection["product_probe_last_state"],
                "strong_signal_observed",
            )

    def test_execution_stage_probe_unsupported_continues_polling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            client = FakeGuestClient(probe_states=["unsupported"])

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="tencent-pc-manager",
                    product_probe_available=True,
                    execution_product_probe_enabled=True,
                    post_execution_collection_delay_seconds=0.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda _seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(client.probe_products, ["tencent-pc-manager"])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            execution = run_state["stages"]["execution"]
            self.assertTrue(execution["execution_product_probe_enabled"])
            self.assertEqual(execution["product_probe_count"], 1)
            self.assertEqual(execution["product_probe_last_state"], "unsupported")
            self.assertFalse(execution["strong_signal_observed"])

    def test_execution_stage_probe_failure_records_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            client = FakeGuestClient(
                probe_error=GuestAgentError("probe failed", source="remote")
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="tencent-pc-manager",
                    product_probe_available=True,
                    execution_product_probe_enabled=True,
                    post_execution_collection_delay_seconds=0.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda _seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            execution = run_state["stages"]["execution"]
            self.assertEqual(execution["product_probe_failed_count"], 1)
            self.assertEqual(execution["product_probe_last_state"], "probe_failed")
            self.assertIn(
                "execution-stage product observation probe failed",
                execution["product_probe_warning"],
            )

    def test_execution_stage_probe_tracks_activity_and_strong_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            client = FakeGuestClient(
                execution_states=["running", "running", "exited_cleanly"],
                probe_states=["activity_observed", "strong_signal_observed"],
            )
            sleeps: list[float] = []

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="tencent-pc-manager",
                    product_probe_available=True,
                    execution_product_probe_enabled=True,
                    execution_product_probe_interval_seconds=4.0,
                    execution_poll_interval_seconds=2.0,
                    execution_poll_timeout_seconds=10.0,
                    post_execution_collection_delay_seconds=0.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(
                client.probe_products,
                ["tencent-pc-manager", "tencent-pc-manager"],
            )
            self.assertGreaterEqual(sleeps.count(2.0), 2)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            execution = run_state["stages"]["execution"]
            self.assertEqual(execution["product_probe_count"], 2)
            self.assertEqual(
                execution["product_probe_first_signal_state"],
                "activity_observed",
            )
            self.assertEqual(
                execution["product_probe_last_state"],
                "strong_signal_observed",
            )
            self.assertTrue(execution["activity_signal_observed"])
            self.assertTrue(execution["strong_signal_observed"])
            self.assertTrue(execution["product_signal_seen_during_execution"])
            self.assertEqual(execution["product_probe_elapsed_seconds"], 4.0)

    def test_execution_strong_signal_selects_short_post_execution_delay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            client = FakeGuestClient(probe_states=["strong_signal_observed"])
            sleeps: list[float] = []

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="tencent-pc-manager",
                    product_probe_available=True,
                    execution_product_probe_enabled=True,
                    product_probe_enabled=True,
                    post_execution_collection_delay_seconds=45.0,
                    post_execution_quarantine_delay_seconds=3.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertIn(3.0, sleeps)
            self.assertNotIn(45.0, sleeps)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            collection = run_state["stages"]["collection"]
            self.assertEqual(
                collection["post_execution_collection_delay_seconds"],
                3.0,
            )
            self.assertEqual(
                collection["post_execution_delay_decision_source"],
                "execution_strong_signal_observed",
            )
            self.assertEqual(
                collection["product_probe_exit_reason"],
                "execution_strong_signal_fixed_delay",
            )

    def test_execution_stage_probe_without_strong_keeps_post_execution_probe(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            client = FakeGuestClient(
                probe_states=["no_signal", "strong_signal_observed"]
            )
            sleeps: list[float] = []

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="tencent-pc-manager",
                    product_probe_available=True,
                    execution_product_probe_enabled=True,
                    product_probe_enabled=True,
                    post_execution_collection_delay_seconds=45.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(
                client.probe_products,
                ["tencent-pc-manager", "tencent-pc-manager"],
            )
            self.assertIn(1.0, sleeps)
            self.assertNotIn(45.0, sleeps)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            collection = run_state["stages"]["collection"]
            self.assertEqual(
                collection["post_execution_delay_decision_source"],
                "default",
            )
            self.assertEqual(
                collection["product_probe_exit_reason"],
                "strong_signal_observed",
            )
            self.assertEqual(
                collection["post_execution_product_probe_elapsed_seconds"],
                1.0,
            )

    def test_execution_stage_probe_summary_survives_execution_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            client = FakeGuestClient(
                execution_states=["running", "running"],
                probe_states=["activity_observed", "activity_observed"],
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="tencent-pc-manager",
                    product_probe_available=True,
                    execution_product_probe_enabled=True,
                    execution_product_probe_interval_seconds=1.0,
                    execution_poll_interval_seconds=1.0,
                    execution_poll_timeout_seconds=1.0,
                    post_execution_collection_delay_seconds=0.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda _seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            execution = run_state["stages"]["execution"]
            self.assertEqual(execution["state"], "timeout_still_running")
            self.assertEqual(execution["observation_exit_reason"], "timeout")
            self.assertEqual(execution["product_probe_count"], 2)
            self.assertEqual(
                execution["product_probe_first_signal_state"],
                "activity_observed",
            )
            self.assertTrue(execution["activity_signal_observed"])
            self.assertFalse(execution["strong_signal_observed"])

    def test_launch_failure_preserves_product_probe_capability_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"MZ harmless placeholder")
            client = FakeGuestClient(
                execute_error=GuestAgentError(
                    "Guest Agent action failed: Desktop Worker execute returned "
                    "HTTP 400: uploaded sample failed to start: OSError "
                    "reason_code=blocked_by_security_product",
                    status_code=400,
                    source="remote",
                ),
                probe_states=["strong_signal_observed"],
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="tencent-pc-manager",
                    product_probe_available=True,
                    product_probe_enabled=True,
                    execution_product_probe_enabled=True,
                    post_execution_collection_delay_seconds=25.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda _seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            execution = run_state["stages"]["execution"]
            self.assertEqual(execution["state"], "launch_failed")
            self.assertTrue(execution["execution_product_probe_enabled"])
            self.assertTrue(execution["product_probe_supported"])
            self.assertEqual(execution["product_probe_count"], 0)
            self.assertEqual(
                execution["observation_exit_reason"],
                "launch_failed_before_polling",
            )
            collection = run_state["stages"]["collection"]
            self.assertEqual(
                collection["product_probe_exit_reason"],
                "strong_signal_observed",
            )

    def test_desktop_worker_execute_timeout_continues_to_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"MZ harmless placeholder")
            client = FakeGuestClient(
                execute_error=GuestAgentError(
                    "Guest Agent cases/case/actions returned HTTP 502 Bad Gateway: "
                    "Desktop Worker execute request failed: TimeoutError",
                    status_code=502,
                    source="remote",
                ),
                probe_states=["strong_signal_observed"],
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="qihoo-360",
                    product_probe_available=True,
                    product_probe_enabled=True,
                    execution_product_probe_enabled=True,
                    post_execution_collection_delay_seconds=25.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda _seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertEqual(client.collection_products, ["qihoo-360"])
            self.assertTrue(result.evidence_bundle_path)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            execution = run_state["stages"]["execution"]
            self.assertEqual(execution["action_status"], "not_started")
            self.assertEqual(execution["state"], "execution_request_timeout")
            self.assertEqual(execution["error_status_code"], 502)
            self.assertEqual(
                execution["observation_exit_reason"],
                "execute_request_timeout_before_polling",
            )
            collection = run_state["stages"]["collection"]
            self.assertEqual(
                collection["product_probe_exit_reason"],
                "strong_signal_observed",
            )
            self.assertEqual(collection["evidence_count"], 1)

    def test_qihoo_execute_error_continues_to_delay_and_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"MZ harmless placeholder")
            sleeps: list[float] = []
            client = FakeGuestClient(
                execute_error=GuestAgentError(
                    "Guest Agent cases/case/actions returned HTTP 502 Bad Gateway: "
                    "Desktop Worker execute request failed: unexpected worker loss",
                    status_code=502,
                    source="remote",
                ),
                probe_states=["strong_signal_observed"],
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="qihoo-360",
                    product_probe_available=True,
                    product_probe_enabled=True,
                    execution_product_probe_enabled=True,
                    post_execution_collection_delay_seconds=25.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertEqual(client.collection_products, ["qihoo-360"])
            self.assertIn(1.0, sleeps)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            execution = run_state["stages"]["execution"]
            self.assertEqual(execution["action_status"], "not_started")
            self.assertEqual(execution["state"], "execution_error")
            self.assertEqual(execution["error_status_code"], 502)
            self.assertEqual(
                execution["observation_exit_reason"],
                "execution_error_before_polling",
            )
            collection = run_state["stages"]["collection"]
            self.assertEqual(
                collection["product_probe_exit_reason"],
                "strong_signal_observed",
            )

    def test_non_qihoo_unknown_execute_error_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"MZ harmless placeholder")
            client = FakeGuestClient(
                execute_error=GuestAgentError(
                    "Guest Agent cases/case/actions returned HTTP 502 Bad Gateway: "
                    "Desktop Worker execute request failed: unexpected worker loss",
                    status_code=502,
                    source="remote",
                ),
                probe_states=["strong_signal_observed"],
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="tencent-pc-manager",
                    product_probe_available=True,
                    product_probe_enabled=True,
                    execution_product_probe_enabled=True,
                    post_execution_collection_delay_seconds=25.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda _seconds: None,
            )

            self.assertEqual(result.final_status, "failed")
            self.assertEqual(client.collection_products, [])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertIn("unexpected worker loss", run_state["errors"][-1]["message"])

    def test_invalid_executable_is_marked_unmatched_instruction_without_delay(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"MZ harmless placeholder")
            sleeps: list[float] = []
            client = FakeGuestClient(
                execute_error=GuestAgentError(
                    "Guest Agent cases/case/actions returned HTTP 400 Bad Request: "
                    "Desktop Worker execute returned HTTP 400: uploaded sample "
                    "failed to start: OSError reason_code=invalid_executable "
                    "winerror=193 errno=8 message=%1 "
                    "不是有效的 Win32 应用程序。",
                    status_code=400,
                    source="remote",
                )
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    post_execution_collection_delay_seconds=45.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertEqual(client.collection_products, ["huorong"])
            self.assertNotIn(45.0, sleeps)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            execution = run_state["stages"]["execution"]
            self.assertEqual(execution["action_status"], "not_started")
            self.assertEqual(execution["state"], "unmatched_instruction")
            self.assertEqual(
                execution["observation_exit_reason"],
                "unmatched_instruction_before_polling",
            )
            collection = run_state["stages"]["collection"]
            self.assertEqual(
                collection["post_execution_collection_delay_seconds"],
                45.0,
            )

    def test_product_probe_unsupported_falls_back_to_fixed_delay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            sleeps: list[float] = []
            client = FakeGuestClient(probe_states=["unsupported"])

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    post_execution_collection_delay_seconds=45.0,
                    product_probe_enabled=True,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(sleeps[-2:], [1.0, 44.0])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                run_state["stages"]["collection"]["product_probe_exit_reason"],
                "unsupported_fallback_to_fixed_delay",
            )

    def test_product_probe_failure_records_warning_and_keeps_case_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.bat"
            sample_path.write_text("echo harmless", encoding="utf-8")
            sleeps: list[float] = []
            client = FakeGuestClient(
                probe_error=GuestAgentError("probe failed", source="remote")
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    post_execution_collection_delay_seconds=45.0,
                    product_probe_enabled=True,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertEqual(sleeps[-2:], [1.0, 44.0])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            collection = run_state["stages"]["collection"]
            self.assertEqual(
                collection["product_probe_exit_reason"],
                "probe_failed_fallback_to_fixed_delay",
            )
            self.assertEqual(collection["product_probe_failed_count"], 1)
            self.assertIn(
                "product observation probe failed",
                run_state["warnings"][-1]["message"],
            )

    def test_single_run_does_not_wait_when_execution_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.ps1"
            sample_path.write_text("Write-Output harmless", encoding="utf-8")
            sleeps: list[float] = []

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    post_execution_collection_delay_seconds=45.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: FakeGuestClient(),
                sleep=sleeps.append,
            )

            self.assertNotIn(45.0, sleeps)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["execution_action_status"], "skipped")
            self.assertEqual(
                run_state["stages"]["collection"][
                    "post_execution_collection_delay_seconds"
                ],
                45.0,
            )

    def test_single_run_waits_after_launch_failure_before_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"MZ harmless placeholder")
            sleeps: list[float] = []
            client = FakeGuestClient(
                status_upload_state="stable",
                execute_error=GuestAgentError(
                    "Guest Agent action failed: Desktop Worker execute returned "
                    "HTTP 400: uploaded sample failed to start: OSError "
                    "reason_code=blocked_by_security_product",
                    status_code=400,
                    source="remote",
                ),
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    post_execution_collection_delay_seconds=45.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertIn(45.0, sleeps)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["execution_action_status"], "not_started")
            self.assertEqual(run_state["execution_action_state"], "launch_failed")
            run_log = (result.run_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn(
                "post-execution collection delay started after launch failure: 45s",
                run_log,
            )

    def test_post_execution_probe_runs_after_launch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.exe"
            sample_path.write_bytes(b"MZ harmless placeholder")
            sleeps: list[float] = []
            client = FakeGuestClient(
                execute_error=GuestAgentError(
                    "Guest Agent action failed: Desktop Worker execute returned "
                    "HTTP 400: uploaded sample failed to start: OSError "
                    "reason_code=blocked_by_security_product",
                    status_code=400,
                    source="remote",
                ),
                probe_states=["strong_signal_observed"],
            )

            result = run_single_case(
                _options(
                    root,
                    sample_path,
                    product_id="tencent-pc-manager",
                    product_probe_available=True,
                    product_probe_enabled=True,
                    execution_product_probe_enabled=True,
                    post_execution_collection_delay_seconds=25.0,
                ),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=sleeps.append,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertEqual(client.probe_products, ["tencent-pc-manager"])
            self.assertIn(1.0, sleeps)
            self.assertNotIn(25.0, sleeps)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            collection = run_state["stages"]["collection"]
            self.assertEqual(
                collection["product_probe_exit_reason"],
                "strong_signal_observed",
            )
            self.assertEqual(
                collection["post_execution_product_probe_elapsed_seconds"], 1.0
            )
            self.assertEqual(
                collection["post_execution_delay_decision_reason"],
                "no execution-stage strong signal observed",
            )

    def test_powershell_script_is_recognized_but_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.ps1"
            sample_path.write_text("Write-Output harmless", encoding="utf-8")
            client = FakeGuestClient(status_upload_state="stable")

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertEqual(client.execute_handler_ids, [])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["execution_action_status"], "skipped")
            self.assertEqual(
                run_state["execution_action_state"],
                "execution_handler_disabled",
            )
            self.assertEqual(
                run_state["stages"]["execution"]["handler_id"],
                "powershell_script",
            )

    def test_unknown_execution_type_is_skipped_before_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.bin"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient(status_upload_state="stable")

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed_with_warnings")
            self.assertEqual(client.execute_handler_ids, [])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["execution_action_status"], "skipped")
            self.assertEqual(
                run_state["execution_action_state"],
                "unsupported_file_type",
            )
            self.assertEqual(
                run_state["stages"]["execution"]["handler_id"],
                "unsupported",
            )


class SingleRunLockTests(TestCase):
    def test_force_unlock_archives_existing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            locks_dir = Path(tmp) / ".locks"
            first = acquire_lock(
                locks_dir,
                instance_id="lhins-example",
                run_id="run-1",
                case_id="case-1",
            )

            with self.assertRaises(InstanceLockedError):
                acquire_lock(
                    locks_dir,
                    instance_id="lhins-example",
                    run_id="run-2",
                    case_id="case-2",
                )

            second = acquire_lock(
                locks_dir,
                instance_id="lhins-example",
                run_id="run-2",
                case_id="case-2",
                force_unlock=True,
            )

            self.assertTrue(second.path.exists())
            self.assertTrue(list(locks_dir.glob("*.stale-*")))
            second.release()
            first.acquired = False

    def test_atomic_lock_write_retries_transient_replace_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "lhins-example.lock"
            original_replace = Path.replace
            replace_calls = 0

            def flaky_replace(self: Path, target_path: Path) -> Path:
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 1:
                    raise PermissionError("transient replace failure")
                return original_replace(self, target_path)

            with patch.object(Path, "replace", flaky_replace):
                _write_json_atomic(
                    target,
                    {"status": "ok"},
                    retry_delay_seconds=0,
                    sleep=lambda _seconds: None,
                )

            self.assertEqual(replace_calls, 2)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"status": "ok"},
            )
            self.assertFalse(list(Path(tmp).glob("*.tmp")))

    def test_atomic_lock_write_cleans_temp_after_retry_exhaustion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "lhins-example.lock"

            def failing_replace(self: Path, target_path: Path) -> Path:
                raise PermissionError("still locked")

            with (
                patch.object(Path, "replace", failing_replace),
                self.assertRaises(PermissionError),
            ):
                _write_json_atomic(
                    target,
                    {"status": "ok"},
                    attempts=2,
                    retry_delay_seconds=0,
                    sleep=lambda _seconds: None,
                )

            self.assertFalse(target.exists())
            self.assertFalse(list(Path(tmp).glob("*.tmp")))


class FakeCloudAdapter:
    supports_execution = False

    def __init__(self) -> None:
        self.calls: list[str] = []

    def restore_snapshot(self, vm: object) -> VMOperationResponse:
        self.calls.append("restore_snapshot")
        return VMOperationResponse(
            status="dry-run",
            task_id="",
            message="restore planned",
            data={"FinalInstanceStatus": {"state": "RUNNING"}},
            dry_run=True,
            provider="tencent-cloud-lighthouse",
        )

    def start_vm(self, vm: object) -> VMOperationResponse:
        self.calls.append("start_vm")
        return VMOperationResponse(
            status="dry-run",
            task_id="",
            message="start planned",
            dry_run=True,
        )

    def stop_vm(self, vm: object) -> VMOperationResponse:
        self.calls.append("stop_vm")
        return VMOperationResponse(
            status="dry-run",
            task_id="",
            message="stop planned",
            dry_run=True,
        )

    def reboot_vm(self, vm: object) -> VMOperationResponse:
        raise AssertionError("single-run should not reboot")

    def get_instance_status(self, vm: object) -> VMOperationResponse:
        self.calls.append("get_instance_status")
        return VMOperationResponse(
            status="dry-run",
            task_id="",
            message="status planned",
            data={"InstanceStatus": {"state": "STOPPED"}},
            dry_run=True,
        )

    def resolve_instance_id(self, vm: object) -> str:
        return "lhins-example"


class FakeInstanceLock:
    def __init__(self, *, fail_on_call: int) -> None:
        self.fail_on_call = fail_on_call
        self.heartbeat_calls = 0
        self.released = False

    def heartbeat(self) -> None:
        self.heartbeat_calls += 1
        if self.heartbeat_calls == self.fail_on_call:
            raise PermissionError("transient heartbeat failure")

    def release(self) -> None:
        self.released = True


class FakeGuestClient:
    def __init__(
        self,
        fail_collect: bool = False,
        status_upload_state: str = "stable",
        readiness_state: str = "ready",
        readiness_error: GuestAgentError | None = None,
        execute_error: GuestAgentError | None = None,
        worker_ready: bool = True,
        worker_reason: str = "",
        execution_states: list[str] | None = None,
        probe_states: list[str] | None = None,
        probe_error: GuestAgentError | None = None,
        execution_status_error: GuestAgentError | None = None,
        case_status_errors: list[GuestAgentError] | None = None,
    ) -> None:
        self.fail_collect = fail_collect
        self.status_upload_state = status_upload_state
        self.readiness_state = readiness_state
        self.readiness_error = readiness_error
        self.execute_error = execute_error
        self.worker_ready = worker_ready
        self.worker_reason = worker_reason
        self.execution_states = list(execution_states or [])
        self.probe_states = list(probe_states or [])
        self.probe_error = probe_error
        self.execution_status_error = execution_status_error
        self.case_status_errors = list(case_status_errors or [])
        self.health_calls = 0
        self.worker_status_calls = 0
        self.case_status_calls = 0
        self.export_timeouts: list[float | None] = []
        self.execute_dry_runs: list[bool] = []
        self.execute_handler_ids: list[str] = []
        self.calls: list[str] = []
        self.prepare_products: list[str] = []
        self.readiness_products: list[str] = []
        self.collection_products: list[str] = []
        self.probe_products: list[str] = []
        self.warmup_products: list[str] = []
        self.upload_md5s: list[str] = []

    def health(self, timeout_seconds: float | None = None) -> GuestAgentResponse:
        self.health_calls += 1
        return GuestAgentResponse(status="ok", message="healthy", data={})

    def worker_status(self, timeout_seconds: float | None = None) -> GuestAgentResponse:
        self.worker_status_calls += 1
        return GuestAgentResponse(
            status="ok",
            message="worker",
            data={
                "desktop_worker_ready": self.worker_ready,
                "desktop_session_ready": self.worker_ready,
                "worker_session_id": 1 if self.worker_ready else 0,
                "desktop_session_state": "active" if self.worker_ready else "unknown",
                "username": "AvTester-Admin",
                "reason": self.worker_reason,
            },
        )

    def warm_up_security_product(
        self,
        product_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        self.calls.append("warm_up_security_product")
        self.warmup_products.append(product_id)
        return GuestAgentResponse(
            status="ok",
            message="desktop worker product warm-up handled",
            data={
                "product_id": product_id,
                "action": "open_main_ui",
                "warmup_state": "started",
                "pid": 9876,
                "client_supplied_path": False,
                "client_supplied_command": False,
                "client_supplied_args": False,
            },
        )

    def prepare_case(
        self,
        case: object,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        self.calls.append("prepare_case")
        self.prepare_products.append(case.product.id)
        return GuestAgentResponse(status="ok", message="prepared", data={})

    def check_security_product_readiness(
        self,
        case_id: str,
        product_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        self.calls.append("check_security_product_readiness")
        self.readiness_products.append(product_id)
        if self.readiness_error is not None:
            raise self.readiness_error
        return GuestAgentResponse(
            status="ok",
            message="security product readiness checked",
            data={
                "case_id": case_id,
                "product_id": product_id,
                "state": self.readiness_state,
                "confidence": "medium",
                "scope": "log_observability",
                "protection_state": "unknown",
                "checked_at_utc": "2026-05-22T00:00:00Z",
                "warnings": ["readiness warning"]
                if self.readiness_state != "ready"
                else [],
                "errors": [],
            },
        )

    def upload_sample(
        self,
        case_id: str,
        sample_id: str,
        file_path: Path,
        sha256: str = "",
        md5: str = "",
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        self.calls.append("upload_sample")
        self.upload_md5s.append(md5)
        return GuestAgentResponse(
            status="ok",
            message="uploaded",
            data={"upload_state": "uploaded"},
        )

    def case_status(
        self,
        case_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        self.case_status_calls += 1
        if self.case_status_errors:
            raise self.case_status_errors.pop(0)
        return GuestAgentResponse(
            status="ok",
            message="status",
            data={"state": {"upload_state": self.status_upload_state}},
        )

    def execute_uploaded_sample(
        self,
        case_id: str,
        sample_id: str,
        expected_sha256: str = "",
        dry_run: bool = True,
        run_id: str = "",
        handler_id: str = "",
    ) -> GuestAgentResponse:
        if self.execute_error is not None:
            raise self.execute_error
        self.execute_dry_runs.append(dry_run)
        self.execute_handler_ids.append(handler_id)
        if not dry_run:
            return GuestAgentResponse(
                status="ok",
                message="started",
                data={"execution_state": "running", "root_pid": 4321},
            )
        return GuestAgentResponse(
            status="ok",
            message="dry run",
            data={"execution_state": "execution_dry_run_checked"},
        )

    def execution_status(
        self,
        case_id: str,
        mark_timeout: bool = False,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        if self.execution_status_error is not None:
            raise self.execution_status_error
        if mark_timeout:
            execution_state = "timeout_still_running"
        else:
            execution_state = (
                self.execution_states.pop(0)
                if self.execution_states
                else "exited_cleanly"
            )
        return GuestAgentResponse(
            status="ok",
            message="execution",
            data={
                "execution_state": execution_state,
                "root_pid": 4321 if execution_state == "running" else None,
                "children": [{"pid": 4322}] if execution_state == "running" else [],
            },
        )

    def collect_logs(
        self,
        case_id: str,
        product_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        self.collection_products.append(product_id)
        if self.fail_collect:
            raise GuestAgentError("collector failed", source="remote")
        return GuestAgentResponse(
            status="ok",
            message="collected",
            data={"evidence_count": 1},
        )

    def probe_collection(
        self,
        case_id: str,
        product_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        self.probe_products.append(product_id)
        if self.probe_error is not None:
            raise self.probe_error
        probe_state = self.probe_states.pop(0) if self.probe_states else "no_signal"
        return GuestAgentResponse(
            status="ok",
            message="probe",
            data={"product_id": product_id, "probe_state": probe_state},
        )

    def case_summary(
        self,
        case_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        return GuestAgentResponse(
            status="ok",
            message="summary",
            data={
                "case_id": case_id,
                "sample_id": "eicar",
                "product_id": self.collection_products[-1]
                if self.collection_products
                else "huorong",
                "verdict": "detected_or_blocked",
                "confidence": "high",
                "summary": "collected evidence",
                "reasons": ["collection evidence_count>0"],
                "timeline": [],
            },
        )

    def export_evidence_bundle(
        self,
        case_id: str,
        output_path: Path,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        self.export_timeouts.append(timeout_seconds)
        output_path.write_bytes(b"fake evidence")
        return GuestAgentResponse(
            status="ok",
            message="saved",
            data={"output_path": str(output_path), "size": 13},
        )


def _options(
    root: Path,
    sample_path: Path,
    *,
    dry_run: bool = False,
    product_id: str = "huorong",
    salvage_timeout: NetworkTimeoutProfile | None = None,
    guest_ready_timeout_seconds: float = 180.0,
    guest_ready_successes: int = 2,
    post_execution_collection_delay_seconds: float = 45.0,
    post_execution_quarantine_delay_seconds: float = 3.0,
    product_warmup_enabled: bool = False,
    product_warmup_cooldown_seconds: float = 0.0,
    product_probe_enabled: bool = False,
    post_execution_probe_interval_seconds: float = 1.0,
    product_probe_available: bool = False,
    execution_product_probe_enabled: bool = False,
    execution_product_probe_interval_seconds: float = 1.0,
    execution_poll_interval_seconds: float = 2.0,
    execution_poll_timeout_seconds: float = 60.0,
    upload_poll_timeout_seconds: float = 0.1,
    defer_final_cleanup: bool = False,
    skip_initial_restore: bool = False,
) -> SingleRunOptions:
    return SingleRunOptions(
        instance_id="lhins-example",
        snapshot_id="lhsnap-example",
        region="ap-singapore",
        sample_name="eicar",
        sample_path=sample_path,
        guest_agent_url="http://127.0.0.1:8080",
        product_id=product_id,
        dry_run=dry_run,
        runs_dir=root / "runs",
        guest_ready_timeout_seconds=guest_ready_timeout_seconds,
        guest_ready_interval_seconds=0.1,
        guest_ready_successes=guest_ready_successes,
        settling_cooldown_seconds=0,
        product_warmup_enabled=product_warmup_enabled,
        product_warmup_cooldown_seconds=product_warmup_cooldown_seconds,
        upload_poll_interval_seconds=0.1,
        upload_poll_timeout_seconds=upload_poll_timeout_seconds,
        execution_poll_interval_seconds=execution_poll_interval_seconds,
        execution_poll_timeout_seconds=execution_poll_timeout_seconds,
        post_execution_collection_delay_seconds=(
            post_execution_collection_delay_seconds
        ),
        post_execution_quarantine_delay_seconds=post_execution_quarantine_delay_seconds,
        product_probe_enabled=product_probe_enabled,
        post_execution_probe_interval_seconds=post_execution_probe_interval_seconds,
        product_probe_available=product_probe_available,
        execution_product_probe_enabled=execution_product_probe_enabled,
        execution_product_probe_interval_seconds=(
            execution_product_probe_interval_seconds
        ),
        salvage_timeout=salvage_timeout or NetworkTimeoutProfile(2, 5),
        defer_final_cleanup=defer_final_cleanup,
        skip_initial_restore=skip_initial_restore,
    )
