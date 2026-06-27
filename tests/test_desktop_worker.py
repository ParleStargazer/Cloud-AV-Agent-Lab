from __future__ import annotations

import sys
import tempfile
import unittest
import hashlib
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from fastapi.testclient import TestClient

    from cloud_av_agent_lab.desktop_worker.app import create_app
    from cloud_av_agent_lab.desktop_worker.lease import issue_execution_lease
    from cloud_av_agent_lab.guest_agent_server.workspace import (
        prepare_case_workspace,
        save_uploaded_sample,
    )
except ModuleNotFoundError:  # pragma: no cover - optional dependency absent
    TestClient = None
    create_app = None
    issue_execution_lease = None
    prepare_case_workspace = None
    save_uploaded_sample = None


TOKEN = "unit-test-worker-token"


@unittest.skipIf(TestClient is None, "fastapi extra is not installed")
class DesktopWorkerTests(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.client = TestClient(
            create_app(
                token=TOKEN,
                workdir=self.workdir,
                bind_host="127.0.0.1",
                app_version="test-version",
            )
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _headers(self, token: str = TOKEN) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_health_success_is_authenticated_and_loopback_scoped(self) -> None:
        with (
            patch(
                "cloud_av_agent_lab.desktop_worker.status.current_process_session_id",
                return_value=1,
            ),
            patch(
                "cloud_av_agent_lab.desktop_worker.status.active_desktop_session_id",
                return_value=1,
            ),
        ):
            response = self.client.get("/health", headers=self._headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["message"], "desktop worker healthy")
        self.assertEqual(payload["data"]["bind_host"], "127.0.0.1")
        self.assertEqual(payload["data"]["worker_session_id"], 1)
        self.assertTrue(payload["data"]["interactive_session"])
        self.assertEqual(payload["data"]["desktop_session_state"], "active")
        self.assertFalse(payload["data"]["busy"])
        self.assertNotIn(TOKEN, response.text)

    def test_health_requires_token(self) -> None:
        self.assertEqual(self.client.get("/health").status_code, 401)

    def test_health_rejects_wrong_token(self) -> None:
        response = self.client.get("/health", headers=self._headers("wrong"))

        self.assertEqual(response.status_code, 401)
        self.assertNotIn(TOKEN, response.text)

    def test_execute_requires_worker_token_and_execution_lease(self) -> None:
        case_id, sample_id, sha256 = _prepare_worker_case(self.workdir)

        missing_auth = self.client.post(
            "/execute",
            json={
                "case_id": case_id,
                "sample_id": sample_id,
                "run_id": "run-1",
                "expected_sha256": sha256,
                "execution_lease": "missing",
            },
        )
        missing_lease = self.client.post(
            "/execute",
            headers=self._headers(),
            json={
                "case_id": case_id,
                "sample_id": sample_id,
                "run_id": "run-1",
                "expected_sha256": sha256,
                "execution_lease": "",
            },
        )

        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(missing_lease.status_code, 400)
        self.assertNotIn(TOKEN, missing_lease.text)

    def test_execute_rejects_arbitrary_path_fields(self) -> None:
        case_id, sample_id, sha256 = _prepare_worker_case(self.workdir)
        lease = _lease(case_id, sample_id, "run-1", sha256)

        response = self.client.post(
            "/execute",
            headers=self._headers(),
            json={
                "case_id": case_id,
                "sample_id": sample_id,
                "run_id": "run-1",
                "expected_sha256": sha256,
                "execution_lease": lease,
                "path": r"C:\Temp\proof.exe",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported fields", response.text)

    def test_execute_unexpected_error_returns_structured_json(self) -> None:
        with patch(
            "cloud_av_agent_lab.desktop_worker.execution.WorkerExecutionRegistry.execute",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.post(
                "/execute",
                headers=self._headers(),
                json={"case_id": "case-001__huorong"},
            )

        self.assertEqual(response.status_code, 500)
        payload = response.json()
        self.assertEqual(
            payload["detail"]["reason_code"],
            "desktop_worker_internal_error",
        )
        self.assertEqual(payload["detail"]["error_type"], "RuntimeError")
        self.assertNotIn(TOKEN, response.text)

    def test_execute_starts_registered_exe_with_minimal_env(self) -> None:
        case_id, sample_id, sha256 = _prepare_worker_case(self.workdir)
        lease = _lease(case_id, sample_id, "run-1", sha256)

        with (
            patch(
                "cloud_av_agent_lab.desktop_worker.execution.subprocess.Popen"
            ) as popen,
            patch.dict(
                "os.environ",
                {
                    "CLOUD_AV_DESKTOP_WORKER_TOKEN": TOKEN,
                    "TENCENTCLOUD_SECRET_KEY": "secret",
                    "HTTP_PROXY": "http://127.0.0.1:7890",
                    "SystemRoot": r"C:\Windows",
                    "TEMP": r"C:\Temp",
                },
                clear=True,
            ),
        ):
            popen.return_value.pid = 4321
            response = self.client.post(
                "/execute",
                headers=self._headers(),
                json={
                    "case_id": case_id,
                    "sample_id": sample_id,
                    "run_id": "run-1",
                    "expected_sha256": sha256,
                    "execution_lease": lease,
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["execution_state"], "running")
        self.assertEqual(data["root_pid"], 4321)
        self.assertEqual(data["handler_id"], "pe_executable")
        args, kwargs = popen.call_args
        self.assertEqual(len(args[0]), 1)
        self.assertTrue(args[0][0].endswith("proof.exe"))
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["cwd"].endswith("sample"))
        self.assertTrue(kwargs["close_fds"])
        child_env = kwargs["env"]
        self.assertIn("SystemRoot", child_env)
        self.assertNotIn("CLOUD_AV_DESKTOP_WORKER_TOKEN", child_env)
        self.assertNotIn("TENCENTCLOUD_SECRET_KEY", child_env)
        self.assertNotIn("HTTP_PROXY", child_env)
        state_path = (
            self.workdir
            / "cases"
            / case_id
            / "worker-state"
            / "worker_execution_state.json"
        )
        self.assertTrue(state_path.exists())

    def test_execute_launch_failure_records_safe_os_error_details(self) -> None:
        case_id, sample_id, sha256 = _prepare_worker_case(self.workdir)
        lease = _lease(case_id, sample_id, "run-1", sha256)
        launch_error = OSError(
            13,
            "Operation did not complete successfully because the file contains "
            "a virus or potentially unwanted software.",
        )
        launch_error.winerror = 225  # type: ignore[attr-defined]

        with patch(
            "cloud_av_agent_lab.desktop_worker.execution.subprocess.Popen",
            side_effect=launch_error,
        ):
            response = self.client.post(
                "/execute",
                headers=self._headers(),
                json={
                    "case_id": case_id,
                    "sample_id": sample_id,
                    "run_id": "run-1",
                    "expected_sha256": sha256,
                    "execution_lease": lease,
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("reason_code=blocked_by_security_product", response.text)
        self.assertIn("winerror=225", response.text)
        state_path = (
            self.workdir
            / "cases"
            / case_id
            / "worker-state"
            / "worker_execution_state.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "launch_failed")
        self.assertEqual(
            state["error_details"]["reason_code"],
            "blocked_by_security_product",
        )
        self.assertEqual(state["error_details"]["winerror"], 225)
        self.assertTrue(state["sample_path_under_case"])

    def test_execute_starts_registered_batch_with_fixed_cmd_template(self) -> None:
        case_id, sample_id, sha256 = _prepare_worker_case(
            self.workdir,
            original_filename="eicar.bat",
            content=b"@echo off\r\necho harmless\r\n",
        )
        lease = _lease(case_id, sample_id, "run-1", sha256)

        with patch(
            "cloud_av_agent_lab.desktop_worker.execution.subprocess.Popen"
        ) as popen:
            popen.return_value.pid = 4321
            response = self.client.post(
                "/execute",
                headers=self._headers(),
                json={
                    "case_id": case_id,
                    "sample_id": sample_id,
                    "run_id": "run-1",
                    "expected_sha256": sha256,
                    "handler_id": "batch_script",
                    "execution_lease": lease,
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["handler_id"], "batch_script")
        self.assertEqual(data["execution_mode"], "script_via_cmd")
        self.assertEqual(data["interpreter"], r"C:\Windows\System32\cmd.exe")
        self.assertFalse(data["client_supplied_command"])
        self.assertFalse(data["client_supplied_args"])
        self.assertFalse(data["client_supplied_path"])
        self.assertTrue(data["hash_verified"])
        args, kwargs = popen.call_args
        self.assertEqual(
            args[0],
            [
                r"C:\Windows\System32\cmd.exe",
                "/d",
                "/c",
                "call",
                str(self.workdir / "cases" / case_id / "sample" / "eicar.bat"),
            ],
        )
        self.assertFalse(kwargs["shell"])

    def test_execute_starts_registered_cmd_with_batch_handler(self) -> None:
        case_id, sample_id, sha256 = _prepare_worker_case(
            self.workdir,
            original_filename="eicar.cmd",
            content=b"@echo off\r\necho harmless\r\n",
        )
        lease = _lease(case_id, sample_id, "run-1", sha256)

        with patch(
            "cloud_av_agent_lab.desktop_worker.execution.subprocess.Popen"
        ) as popen:
            popen.return_value.pid = 4321
            response = self.client.post(
                "/execute",
                headers=self._headers(),
                json={
                    "case_id": case_id,
                    "sample_id": sample_id,
                    "run_id": "run-1",
                    "expected_sha256": sha256,
                    "handler_id": "batch_script",
                    "execution_lease": lease,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["handler_id"], "batch_script")
        self.assertTrue(popen.call_args.args[0][-1].endswith("eicar.cmd"))

    def test_powershell_script_is_recognized_but_disabled(self) -> None:
        case_id, sample_id, sha256 = _prepare_worker_case(
            self.workdir,
            original_filename="sample.ps1",
            content=b"Write-Output harmless\r\n",
        )
        lease = _lease(case_id, sample_id, "run-1", sha256)

        response = self.client.post(
            "/execute",
            headers=self._headers(),
            json={
                "case_id": case_id,
                "sample_id": sample_id,
                "run_id": "run-1",
                "expected_sha256": sha256,
                "handler_id": "powershell_script",
                "execution_lease": lease,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("execution_handler_disabled", response.text)

    def test_unknown_extension_is_unsupported(self) -> None:
        case_id, sample_id, sha256 = _prepare_worker_case(
            self.workdir,
            original_filename="sample.bin",
            content=b"harmless",
        )
        lease = _lease(case_id, sample_id, "run-1", sha256)

        response = self.client.post(
            "/execute",
            headers=self._headers(),
            json={
                "case_id": case_id,
                "sample_id": sample_id,
                "run_id": "run-1",
                "expected_sha256": sha256,
                "handler_id": "unsupported",
                "execution_lease": lease,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported_file_type", response.text)

    def test_handler_id_must_match_registered_sample_type(self) -> None:
        case_id, sample_id, sha256 = _prepare_worker_case(
            self.workdir,
            original_filename="proof.exe",
        )
        lease = _lease(case_id, sample_id, "run-1", sha256)

        response = self.client.post(
            "/execute",
            headers=self._headers(),
            json={
                "case_id": case_id,
                "sample_id": sample_id,
                "run_id": "run-1",
                "expected_sha256": sha256,
                "handler_id": "batch_script",
                "execution_lease": lease,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("handler_id does not match", response.text)

    def test_execution_status_reports_registered_process_exit(self) -> None:
        case_id, sample_id, sha256 = _prepare_worker_case(self.workdir)
        lease = _lease(case_id, sample_id, "run-1", sha256)

        with patch(
            "cloud_av_agent_lab.desktop_worker.execution.subprocess.Popen"
        ) as popen:
            popen.return_value.pid = 4321
            popen.return_value.poll.return_value = 0
            self.client.post(
                "/execute",
                headers=self._headers(),
                json={
                    "case_id": case_id,
                    "sample_id": sample_id,
                    "run_id": "run-1",
                    "expected_sha256": sha256,
                    "execution_lease": lease,
                },
            )
            response = self.client.get(
                f"/execution-status/{case_id}",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["execution_state"], "exited_cleanly")
        self.assertEqual(data["exit_code"], 0)

    def test_execution_status_unexpected_error_returns_structured_json(self) -> None:
        with patch(
            "cloud_av_agent_lab.desktop_worker.execution.WorkerExecutionRegistry.execution_status",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.get(
                "/execution-status/case-001__huorong",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 500)
        payload = response.json()
        self.assertEqual(
            payload["detail"]["reason_code"],
            "desktop_worker_internal_error",
        )
        self.assertEqual(payload["detail"]["error_type"], "RuntimeError")
        self.assertNotIn(TOKEN, response.text)

    def test_product_warmup_opens_qihoo_360_fixed_main_ui(self) -> None:
        with (
            patch(
                "cloud_av_agent_lab.desktop_worker.product_warmup.Path.is_file",
                return_value=True,
            ),
            patch(
                "cloud_av_agent_lab.desktop_worker.product_warmup.subprocess.Popen"
            ) as popen,
        ):
            popen.return_value.pid = 9876
            response = self.client.post(
                "/product-actions/warm-up/qihoo-360",
                headers=self._headers(),
                json={},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["product_id"], "qihoo-360")
        self.assertEqual(data["action"], "open_main_ui")
        self.assertEqual(data["warmup_state"], "started")
        self.assertEqual(data["pid"], 9876)
        self.assertFalse(data["client_supplied_path"])
        self.assertFalse(data["client_supplied_command"])
        self.assertFalse(data["client_supplied_args"])
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [r"C:\Program Files (x86)\360\360Safe\360Safe.exe"])
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["close_fds"])

    def test_product_warmup_rejects_client_supplied_command_fields(self) -> None:
        response = self.client.post(
            "/product-actions/warm-up/qihoo-360",
            headers=self._headers(),
            json={"path": r"C:\Temp\anything.exe", "args": ["/unsafe"]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported product warm-up fields", response.text)
        self.assertIn("path", response.text)
        self.assertIn("args", response.text)


def _prepare_worker_case(
    workdir: Path,
    *,
    original_filename: str = "proof.exe",
    content: bytes = b"MZ harmless desktop worker placeholder",
) -> tuple[str, str, str]:
    case_id = "case-001__huorong"
    sample_id = "case-001"
    sha256 = hashlib.sha256(content).hexdigest()
    prepare_case_workspace(
        workdir,
        {
            "case": {"id": case_id},
            "sample": {"id": sample_id, "sha256": sha256},
            "vm": {"id": "vm-1"},
            "product": {"id": "huorong"},
        },
    )
    save_uploaded_sample(
        workdir=workdir,
        case_id=case_id,
        content=content,
        sample_id=sample_id,
        sha256=sha256,
        original_filename=original_filename,
    )
    return case_id, sample_id, sha256


def _lease(case_id: str, sample_id: str, run_id: str, sha256: str) -> str:
    return issue_execution_lease(
        secret=TOKEN,
        case_id=case_id,
        sample_id=sample_id,
        run_id=run_id,
        expected_sha256=sha256,
        ttl_seconds=60,
    )
