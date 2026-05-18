from __future__ import annotations

import sys
import tempfile
import unittest
import hashlib
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


def _prepare_worker_case(workdir: Path) -> tuple[str, str, str]:
    case_id = "case-001__huorong"
    sample_id = "case-001"
    content = b"MZ harmless desktop worker placeholder"
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
        original_filename="proof.exe",
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
