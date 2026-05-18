from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.adapters.cloud import VMOperationResponse
from cloud_av_agent_lab.adapters.guest_agent_client import (
    GuestAgentError,
    GuestAgentResponse,
)
from cloud_av_agent_lab.orchestration.locks import (
    InstanceLockedError,
    acquire_lock,
    lock_file_for,
)
from cloud_av_agent_lab.orchestration.single_run import (
    SingleRunOptions,
    run_single_case,
)
from cloud_av_agent_lab.orchestration.timeout import NetworkTimeoutProfile


class SingleRunTests(TestCase):
    def test_single_run_generates_artifacts_and_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.txt"
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
            generated_config = result.generated_config_path.read_text(encoding="utf-8")
            self.assertIn('mode = "real"', generated_config)
            self.assertIn("dry_run = false", generated_config)
            self.assertIn("[guest_agent.execution]", generated_config)
            self.assertIn("enabled = true", generated_config)
            self.assertIn("[guest_agent.desktop_worker]", generated_config)
            self.assertIn('base_url = "http://127.0.0.1:8001"', generated_config)
            self.assertNotIn("agent-secret", generated_config)

            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["final_status"], "completed")
            self.assertEqual(run_state["evidence_export_status"], "saved")
            self.assertEqual(run_state["cleanup_status"], "dry_run")
            self.assertTrue(run_state["desktop_worker_ready"])
            self.assertEqual(run_state["desktop_session_state"], "active")

    def test_single_run_dry_run_generates_mock_config_and_dry_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.bin"
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

    def test_single_run_fails_before_delivery_when_worker_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.bin"
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

    def test_fast_fail_salvage_runs_after_case_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.bin"
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

            self.assertEqual(result.final_status, "failed")
            self.assertTrue(result.evidence_bundle_path)
            self.assertTrue(result.evidence_bundle_path.is_file())
            self.assertEqual(client.export_timeouts[-1], 5)
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["evidence_export_status"], "saved")
            self.assertTrue(run_state["case_started"])

    def test_removed_after_save_skips_execution_but_continues_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.bin"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient(status_upload_state="removed_after_save")

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(client.execute_dry_runs, [])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["post_upload_state"], "removed_after_save")
            self.assertEqual(run_state["execution_action_status"], "skipped")
            self.assertEqual(
                run_state["execution_action_state"],
                "skipped_removed_after_save",
            )
            self.assertEqual(run_state["evidence_export_status"], "saved")

    def test_locked_or_busy_skips_execution_but_continues_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.bin"
            sample_path.write_bytes(b"harmless")
            client = FakeGuestClient(status_upload_state="locked_or_busy")

            result = run_single_case(
                _options(root, sample_path),
                cloud_adapter_factory=lambda *args, **kwargs: FakeCloudAdapter(),
                guest_client_factory=lambda config: client,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result.final_status, "completed")
            self.assertEqual(client.execute_dry_runs, [])
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["post_upload_state"], "locked_or_busy")
            self.assertEqual(run_state["execution_action_status"], "skipped")
            self.assertEqual(
                run_state["execution_action_state"],
                "skipped_locked_or_busy",
            )

    def test_nonfatal_execute_error_continues_to_collection_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "sample.bin"
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

            self.assertEqual(result.final_status, "completed")
            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["post_upload_state"], "stable")
            self.assertEqual(run_state["execution_action_status"], "not_started")
            self.assertEqual(
                run_state["execution_action_state"],
                "sample_missing_before_execution",
            )
            self.assertEqual(run_state["evidence_export_status"], "saved")


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


class FakeGuestClient:
    def __init__(
        self,
        fail_collect: bool = False,
        status_upload_state: str = "stable",
        execute_error: GuestAgentError | None = None,
        worker_ready: bool = True,
        worker_reason: str = "",
    ) -> None:
        self.fail_collect = fail_collect
        self.status_upload_state = status_upload_state
        self.execute_error = execute_error
        self.worker_ready = worker_ready
        self.worker_reason = worker_reason
        self.health_calls = 0
        self.worker_status_calls = 0
        self.export_timeouts: list[float | None] = []
        self.execute_dry_runs: list[bool] = []

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
                "username": "avtest",
                "reason": self.worker_reason,
            },
        )

    def prepare_case(
        self,
        case: object,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        return GuestAgentResponse(status="ok", message="prepared", data={})

    def upload_sample(
        self,
        case_id: str,
        sample_id: str,
        file_path: Path,
        sha256: str = "",
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
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
    ) -> GuestAgentResponse:
        if self.execute_error is not None:
            raise self.execute_error
        self.execute_dry_runs.append(dry_run)
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
        return GuestAgentResponse(
            status="ok",
            message="execution",
            data={"execution_state": "exited_cleanly", "children": []},
        )

    def collect_logs(
        self,
        case_id: str,
        product_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        if self.fail_collect:
            raise GuestAgentError("collector failed", source="remote")
        return GuestAgentResponse(
            status="ok",
            message="collected",
            data={"evidence_count": 1},
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
                "product_id": "huorong",
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
    salvage_timeout: NetworkTimeoutProfile | None = None,
    guest_ready_timeout_seconds: float = 120.0,
    guest_ready_successes: int = 2,
) -> SingleRunOptions:
    return SingleRunOptions(
        instance_id="lhins-example",
        snapshot_id="lhsnap-example",
        region="ap-singapore",
        sample_name="eicar",
        sample_path=sample_path,
        guest_agent_url="http://127.0.0.1:8080",
        dry_run=dry_run,
        runs_dir=root / "runs",
        guest_ready_timeout_seconds=guest_ready_timeout_seconds,
        guest_ready_interval_seconds=0.1,
        guest_ready_successes=guest_ready_successes,
        settling_cooldown_seconds=0,
        upload_initial_wait_seconds=0,
        upload_poll_interval_seconds=0.1,
        upload_poll_timeout_seconds=0.1,
        salvage_timeout=salvage_timeout or NetworkTimeoutProfile(2, 5),
    )
