from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from fastapi.testclient import TestClient

    from cloud_av_agent_lab.guest_agent_server.app import create_app
    from cloud_av_agent_lab.guest_agent_server.collectors.huorong import (
        HuorongLogCollector,
    )
    from cloud_av_agent_lab.guest_agent_server.desktop_worker_client import (
        DesktopWorkerClientError,
        DesktopWorkerResponse,
        DesktopWorkerStatus,
    )
    from cloud_av_agent_lab.guest_agent_server.workspace import FileProbe
except ModuleNotFoundError:  # pragma: no cover - optional dependency absent
    TestClient = None
    create_app = None
    FileProbe = None
    HuorongLogCollector = None
    DesktopWorkerClientError = None
    DesktopWorkerResponse = None
    DesktopWorkerStatus = None


TOKEN = "unit-test-token"
UPLOAD_TOKEN = "unit-test-upload-token"
EXECUTION_TOKEN = "unit-test-execution-token"


@unittest.skipIf(TestClient is None, "fastapi extra is not installed")
class GuestAgentServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.client = TestClient(
            create_app(
                workdir=self.workdir,
                token=TOKEN,
                upload_token=UPLOAD_TOKEN,
                app_version="test-version",
            )
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _headers(self, token: str = TOKEN) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _upload_headers(self) -> dict[str, str]:
        return {
            **self._headers(),
            "X-Upload-Token": UPLOAD_TOKEN,
            "X-Sample-Id": "case-001",
            "X-Sample-Sha256": "0" * 64,
            "X-Original-Filename": "eicar.txt",
        }

    def _execution_headers(self, token: str = EXECUTION_TOKEN) -> dict[str, str]:
        return {
            **self._headers(),
            "X-Execution-Token": token,
        }

    def _execution_client(self) -> TestClient:
        return TestClient(
            create_app(
                workdir=self.workdir,
                token=TOKEN,
                upload_token=UPLOAD_TOKEN,
                execution_enabled=True,
                execution_token=EXECUTION_TOKEN,
                desktop_worker_required_for_execution=False,
                app_version="test-version",
            )
        )

    def test_health_success(self) -> None:
        response = self.client.get("/health", headers=self._headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["message"], "guest agent healthy")
        self.assertEqual(payload["data"]["agent"], "cloud-av-agent-lab")
        self.assertEqual(payload["data"]["version"], "test-version")
        self.assertIn("time_utc", payload["data"])

    def test_system_info_success_does_not_leak_token(self) -> None:
        response = self.client.get("/system-info", headers=self._headers())

        self.assertEqual(response.status_code, 200)
        payload_text = response.text
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertIn("hostname", payload["data"])
        self.assertIn("platform", payload["data"])
        self.assertIn("python_version", payload["data"])
        self.assertEqual(payload["data"]["workdir"], str(self.workdir))
        self.assertNotIn(TOKEN, payload_text)

    def test_worker_status_disabled_is_clear(self) -> None:
        response = self.client.get("/worker/status", headers=self._headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["data"]["desktop_worker_enabled"])
        self.assertFalse(payload["data"]["desktop_worker_ready"])
        self.assertIn("disabled", payload["data"]["reason"])
        self.assertNotIn(TOKEN, response.text)

    def test_worker_status_enabled_reports_ready(self) -> None:
        client = TestClient(
            create_app(
                workdir=self.workdir,
                token=TOKEN,
                upload_token=UPLOAD_TOKEN,
                desktop_worker_enabled=True,
                desktop_worker_token="worker-token",
                desktop_worker_expected_user="avtest",
                app_version="test-version",
            )
        )
        with patch(
            "cloud_av_agent_lab.guest_agent_server.app.DesktopWorkerClient.health",
            return_value=DesktopWorkerStatus(
                ready=True,
                data={
                    "worker_pid": 4321,
                    "worker_session_id": 1,
                    "interactive_session": True,
                    "desktop_session_state": "active",
                    "username": "avtest",
                    "bind_host": "127.0.0.1",
                    "version": "test-version",
                    "busy": False,
                },
            ),
        ):
            response = client.get("/worker/status", headers=self._headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["data"]["desktop_worker_ready"])
        self.assertTrue(payload["data"]["desktop_session_ready"])
        self.assertEqual(payload["data"]["worker_session_id"], 1)
        self.assertNotIn("worker-token", response.text)

    def test_worker_status_enabled_reports_not_ready_on_worker_error(self) -> None:
        client = TestClient(
            create_app(
                workdir=self.workdir,
                token=TOKEN,
                upload_token=UPLOAD_TOKEN,
                desktop_worker_enabled=True,
                desktop_worker_token="worker-token",
                app_version="test-version",
            )
        )
        with patch(
            "cloud_av_agent_lab.guest_agent_server.app.DesktopWorkerClient.health",
            side_effect=DesktopWorkerClientError("Desktop Worker returned HTTP 401"),
        ):
            response = client.get("/worker/status", headers=self._headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["data"]["desktop_worker_ready"])
        self.assertEqual(payload["data"]["worker_error_source"], "network")
        self.assertNotIn("worker-token", response.text)

    def test_prepare_case_creates_workspace_and_case_json(self) -> None:
        response = self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["message"], "case workspace prepared")
        workspace = Path(payload["data"]["workspace"])
        case_file = workspace / "case.json"
        state_file = workspace / "case_state.json"
        events_file = workspace / "events.jsonl"
        self.assertTrue(workspace.is_dir())
        self.assertTrue(case_file.is_file())
        self.assertTrue(state_file.is_file())
        self.assertTrue(events_file.is_file())
        self.assertTrue((workspace / "case_report.json").is_file())
        saved = json.loads(case_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["case"]["id"], "case-001__tencent-pc-manager")
        self.assertEqual(
            saved["sample"]["cloud_object_uri"],
            "cos://bucket/redacted/case-001.bin",
        )
        state = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "prepared")
        self.assertEqual(state["upload_state"], "not_uploaded")
        events = _read_events(events_file)
        self.assertEqual(events[-1]["event_type"], "case_prepared")

    def test_missing_token_returns_401(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 401)

    def test_wrong_token_returns_401(self) -> None:
        response = self.client.get("/health", headers=self._headers("wrong-token"))

        self.assertEqual(response.status_code, 401)
        self.assertNotIn(TOKEN, response.text)

    def test_prepare_case_rejects_path_traversal_case_id(self) -> None:
        payload = _prepare_payload()
        payload["case"]["id"] = "../escape"

        response = self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.workdir.parent / "escape").exists())

    def test_prepare_case_only_persists_metadata(self) -> None:
        response = self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        self.assertEqual(response.status_code, 200)
        workspace = Path(response.json()["data"]["workspace"])
        files = sorted(path.name for path in workspace.iterdir())
        self.assertEqual(
            files,
            ["case.json", "case_report.json", "case_state.json", "events.jsonl"],
        )
        saved_text = (workspace / "case.json").read_text(encoding="utf-8")
        self.assertIn("cos://bucket/redacted/case-001.bin", saved_text)
        self.assertNotIn("local_path", saved_text)
        self.assertNotIn("execute", saved_text.casefold())

    def test_upload_sample_success(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        response = self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["message"], "sample uploaded")
        self.assertEqual(payload["data"]["sample_id"], "case-001")
        self.assertEqual(payload["data"]["upload_state"], "uploaded")
        self.assertTrue(payload["data"]["saved_once"])
        self.assertTrue(payload["data"]["metadata_saved"])
        self.assertTrue(payload["data"]["post_write_exists"])
        self.assertFalse(payload["data"]["removed_after_save"])
        self.assertFalse(payload["data"]["locked_or_busy"])
        self.assertFalse(payload["data"]["stable"])
        sample_dir = Path(payload["data"]["sample_dir"])
        self.assertTrue((sample_dir / "eicar.txt").is_file())
        metadata = json.loads((sample_dir / "sample.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["size"], len(b"EICAR harmless placeholder"))
        self.assertEqual(metadata["original_filename"], "eicar.txt")
        self.assertEqual(metadata["upload_state"], "uploaded")

    def test_upload_sample_writes_observable_logs(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        with self.assertLogs(
            "cloud_av_agent_lab.guest_agent_server.workspace",
            level="INFO",
        ) as logs:
            response = self.client.post(
                "/cases/case-001__tencent-pc-manager/sample",
                headers=self._upload_headers(),
                content=b"EICAR harmless placeholder",
            )

        self.assertEqual(response.status_code, 200)
        log_text = "\n".join(logs.output)
        self.assertIn("sample upload saved once", log_text)
        self.assertNotIn("post-upload heartbeat", log_text)
        self.assertNotIn(TOKEN, log_text)
        self.assertNotIn(UPLOAD_TOKEN, log_text)

    def test_case_status_removed_after_save_state(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        upload = self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )
        self.assertEqual(upload.status_code, 200)

        with patch(
            "cloud_av_agent_lab.guest_agent_server.workspace.sample_status._probe_sample_current_status",
            return_value=FileProbe(exists=False, stat_ok=False),
        ):
            response = self.client.get(
                "/cases/case-001__tencent-pc-manager/status",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        state = data["state"]
        self.assertEqual(state["upload_state"], "removed_after_save")
        self.assertTrue(state["sample"]["saved_once"])
        self.assertFalse(state["sample"]["post_write_exists"])
        self.assertTrue(state["sample"]["removed_after_save"])
        self.assertFalse(state["sample"]["locked_or_busy"])
        self.assertFalse(state["sample"]["stable"])
        workspace = Path(data["workspace"])
        state = json.loads((workspace / "case_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["upload_state"], "removed_after_save")
        self.assertTrue(state["sample"]["removed_after_save"])
        events = _read_events(workspace / "events.jsonl")
        event_types = [event["event_type"] for event in events]
        self.assertIn("sample_removed_after_save", event_types)

    def test_case_status_locked_or_busy_state(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        upload = self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )
        self.assertEqual(upload.status_code, 200)

        with patch(
            "cloud_av_agent_lab.guest_agent_server.workspace.sample_status._probe_sample_current_status",
            return_value=FileProbe(
                exists=True,
                stat_ok=False,
                error="PermissionError",
                probe_kind="status",
            ),
        ):
            response = self.client.get(
                "/cases/case-001__tencent-pc-manager/status",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        state = data["state"]
        self.assertEqual(state["upload_state"], "locked_or_busy")
        self.assertTrue(state["sample"]["saved_once"])
        self.assertTrue(state["sample"]["post_write_exists"])
        self.assertFalse(state["sample"]["removed_after_save"])
        self.assertTrue(state["sample"]["locked_or_busy"])
        self.assertFalse(state["sample"]["stable"])
        workspace = Path(data["workspace"])
        events = _read_events(workspace / "events.jsonl")
        event_types = [event["event_type"] for event in events]
        self.assertIn("sample_locked_or_busy", event_types)
        self.assertIn("sample_post_upload_check", event_types)

    def test_upload_sample_does_not_run_status_probe(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        with patch(
            "cloud_av_agent_lab.guest_agent_server.workspace.sample_status._probe_sample_current_status"
        ) as status_probe:
            response = self.client.post(
                "/cases/case-001__tencent-pc-manager/sample",
                headers=self._upload_headers(),
                content=b"EICAR harmless placeholder",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["upload_state"], "uploaded")
        status_probe.assert_not_called()

    def test_upload_sample_missing_agent_token_returns_401(self) -> None:
        response = self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers={
                "X-Upload-Token": UPLOAD_TOKEN,
                "X-Sample-Id": "case-001",
            },
            content=b"placeholder",
        )

        self.assertEqual(response.status_code, 401)

    def test_upload_sample_missing_upload_token_returns_401(self) -> None:
        response = self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers={
                **self._headers(),
                "X-Sample-Id": "case-001",
            },
            content=b"placeholder",
        )

        self.assertEqual(response.status_code, 401)

    def test_upload_sample_wrong_upload_token_returns_403(self) -> None:
        headers = self._upload_headers()
        headers["X-Upload-Token"] = "wrong-upload-token"

        response = self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=headers,
            content=b"placeholder",
        )

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(UPLOAD_TOKEN, response.text)

    def test_upload_sample_missing_case_workspace_returns_404(self) -> None:
        response = self.client.post(
            "/cases/missing-case/sample",
            headers=self._upload_headers(),
            content=b"placeholder",
        )

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertIn("guest-prepare-case", payload["detail"])

    def test_upload_sample_rejects_path_traversal_case_id(self) -> None:
        response = self.client.post(
            "/cases/..%2Fescape/sample",
            headers=self._upload_headers(),
            content=b"placeholder",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.workdir.parent / "escape").exists())

    def test_upload_sample_only_saves_file_and_metadata(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        response = self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )

        self.assertEqual(response.status_code, 200)
        sample_dir = Path(response.json()["data"]["sample_dir"])
        files = sorted(path.name for path in sample_dir.iterdir())
        self.assertEqual(files, ["eicar.txt", "sample.json"])
        metadata_text = (sample_dir / "sample.json").read_text(encoding="utf-8")
        self.assertNotIn("execute", metadata_text.casefold())
        self.assertNotIn("command", metadata_text.casefold())

    def test_case_status_success_returns_state_and_recent_events(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )

        response = self.client.get(
            "/cases/case-001__tencent-pc-manager/status",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["case_id"], "case-001__tencent-pc-manager")
        self.assertEqual(data["state"]["upload_state"], "stable")
        self.assertEqual(data["sample_metadata"]["upload_state"], "stable")
        event_types = [event["event_type"] for event in data["events"]]
        self.assertIn("case_prepared", event_types)
        self.assertIn("sample_stable_after_upload", event_types)

    def test_case_status_missing_case_returns_404(self) -> None:
        response = self.client.get(
            "/cases/missing-case/status",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("guest-prepare-case", response.json()["detail"])

    def test_case_status_rejects_path_traversal_case_id(self) -> None:
        response = self.client.get(
            "/cases/..%2Fescape/status",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.workdir.parent / "escape").exists())

    def test_case_status_does_not_return_sample_content_or_token(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=self._upload_headers(),
            content=b"DO-NOT-READ-SAMPLE-CONTENT",
        )

        response = self.client.get(
            "/cases/case-001__tencent-pc-manager/status",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertNotIn("DO-NOT-READ-SAMPLE-CONTENT", body)
        self.assertNotIn(TOKEN, body)
        self.assertNotIn(UPLOAD_TOKEN, body)

    def test_case_report_success_generates_delivery_report(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )
        self.client.get(
            "/cases/case-001__tencent-pc-manager/status",
            headers=self._headers(),
        )

        response = self.client.get(
            "/cases/case-001__tencent-pc-manager/report",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        report = payload["data"]
        self.assertEqual(payload["message"], "case report loaded")
        self.assertEqual(report["case_id"], "case-001__tencent-pc-manager")
        self.assertEqual(report["sample_id"], "case-001")
        self.assertEqual(report["vm_id"], "sg-win10")
        self.assertEqual(report["product_id"], "tencent-pc-manager")
        self.assertEqual(report["upload_state"], "stable")
        self.assertTrue(report["saved_once"])
        self.assertTrue(report["post_write_exists"])
        self.assertFalse(report["removed_after_save"])
        self.assertFalse(report["locked_or_busy"])
        self.assertTrue(report["stable"])
        self.assertEqual(report["original_filename"], "eicar.txt")
        self.assertEqual(report["sha256"], "0" * 64)
        self.assertEqual(report["size"], len(b"EICAR harmless placeholder"))
        self.assertTrue(report["prepared_at_utc"])
        self.assertTrue(report["uploaded_at_utc"])
        self.assertTrue(report["updated_at_utc"])
        self.assertGreaterEqual(len(report["recent_events"]), 1)
        report_file = (
            self.workdir / "cases" / "case-001__tencent-pc-manager" / "case_report.json"
        )
        self.assertTrue(report_file.is_file())

    def test_case_report_missing_case_returns_404(self) -> None:
        response = self.client.get(
            "/cases/missing-case/report",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("guest-prepare-case", response.json()["detail"])

    def test_case_report_rejects_path_traversal_case_id(self) -> None:
        response = self.client.get(
            "/cases/..%2Fescape/report",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.workdir.parent / "escape").exists())

    def test_case_report_does_not_read_sample_content_or_leak_token(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=self._upload_headers(),
            content=b"DO-NOT-READ-SAMPLE-CONTENT",
        )

        response = self.client.get(
            "/cases/case-001__tencent-pc-manager/report",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertNotIn("DO-NOT-READ-SAMPLE-CONTENT", body)
        self.assertNotIn(TOKEN, body)
        self.assertNotIn(UPLOAD_TOKEN, body)

    def test_action_execute_uploaded_sample_is_disabled_by_default(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        response = self.client.post(
            "/cases/case-001__tencent-pc-manager/actions",
            headers=self._headers(),
            json={"action": "execute_uploaded_sample"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertFalse(data["execution_enabled"])
        self.assertEqual(data["execution_state"], "execution_disabled")
        self.assertIn("no sample was executed", data["message"])

    def test_execution_status_not_started_for_prepared_case(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        response = self.client.get(
            "/cases/case-001__tencent-pc-manager/execution-status",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["execution_state"], "not_started")
        self.assertIsNone(data["root_pid"])
        self.assertEqual(data["children"], [])

    def test_execution_status_missing_case_returns_404(self) -> None:
        response = self.client.get(
            "/cases/missing-case/execution-status",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("guest-prepare-case", response.json()["detail"])

    def test_action_whitelist_rejects_unknown_action(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        response = self.client.post(
            "/cases/case-001__tencent-pc-manager/actions",
            headers=self._headers(),
            json={"action": "run-command"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not allowed", response.json()["detail"])

    def test_action_rejects_arbitrary_command_path_and_shell_args(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        for forbidden_payload in (
            {"action": "dry_run_execute_uploaded_sample", "command": "calc.exe"},
            {"action": "dry_run_execute_uploaded_sample", "path": r"C:\Temp\x.exe"},
            {"action": "dry_run_execute_uploaded_sample", "args": ["/c", "whoami"]},
            {"action": "dry_run_execute_uploaded_sample", "shell": "powershell"},
        ):
            response = self.client.post(
                "/cases/case-001__tencent-pc-manager/actions",
                headers=self._headers(),
                json=forbidden_payload,
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("forbidden execution fields", response.json()["detail"])

    def test_action_dry_run_checks_metadata_without_execution(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        self.client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )

        response = self.client.post(
            "/cases/case-001__tencent-pc-manager/actions",
            headers=self._headers(),
            json={
                "action": "dry_run_execute_uploaded_sample",
                "sample_id": "case-001",
                "expected_sha256": "0" * 64,
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["execution_state"], "execution_dry_run_checked")
        self.assertTrue(data["expected_sha256_match"])
        self.assertTrue(data["sample_path_under_case"])
        self.assertIn("no sample was executed", data["message"])
        events_file = (
            self.workdir / "cases" / "case-001__tencent-pc-manager" / "events.jsonl"
        )
        event_types = [event["event_type"] for event in _read_events(events_file)]
        self.assertIn("execution_dry_run_checked", event_types)

    def test_action_execution_enabled_requires_execution_token(self) -> None:
        client = self._execution_client()
        client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        missing = client.post(
            "/cases/case-001__tencent-pc-manager/actions",
            headers=self._headers(),
            json={"action": "execute_uploaded_sample"},
        )
        wrong = client.post(
            "/cases/case-001__tencent-pc-manager/actions",
            headers=self._execution_headers("wrong-execution-token"),
            json={"action": "execute_uploaded_sample"},
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 403)
        self.assertNotIn(EXECUTION_TOKEN, missing.text)
        self.assertNotIn(EXECUTION_TOKEN, wrong.text)

    def test_action_execution_requires_ready_desktop_worker_by_default(self) -> None:
        client = TestClient(
            create_app(
                workdir=self.workdir,
                token=TOKEN,
                upload_token=UPLOAD_TOKEN,
                execution_enabled=True,
                execution_token=EXECUTION_TOKEN,
                app_version="test-version",
            )
        )
        client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        response = client.post(
            "/cases/case-001__tencent-pc-manager/actions",
            headers=self._execution_headers(),
            json={
                "action": "execute_uploaded_sample",
                "sample_id": "case-001",
                "expected_sha256": "0" * 64,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("desktop worker", response.json()["detail"])

    def test_action_execute_uploaded_sample_forwards_to_desktop_worker(self) -> None:
        client = TestClient(
            create_app(
                workdir=self.workdir,
                token=TOKEN,
                upload_token=UPLOAD_TOKEN,
                execution_enabled=True,
                execution_token=EXECUTION_TOKEN,
                desktop_worker_enabled=True,
                desktop_worker_token="worker-token",
                desktop_worker_expected_user="avtest",
                app_version="test-version",
            )
        )
        upload_headers = self._upload_headers()
        upload_headers["X-Original-Filename"] = "proof.exe"
        client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=upload_headers,
            content=b"MZ harmless placeholder",
        )

        with (
            patch(
                "cloud_av_agent_lab.guest_agent_server.app.DesktopWorkerClient.health",
                return_value=DesktopWorkerStatus(
                    ready=True,
                    data={
                        "worker_pid": 111,
                        "worker_session_id": 1,
                        "interactive_session": True,
                        "desktop_session_state": "active",
                        "username": "avtest",
                        "busy": False,
                    },
                ),
            ),
            patch(
                "cloud_av_agent_lab.guest_agent_server.app.DesktopWorkerClient.execute",
                return_value=DesktopWorkerResponse(
                    status="ok",
                    message="started",
                    data={
                        "action": "execute_uploaded_sample",
                        "execution_state": "running",
                        "message": "uploaded sample process started by Desktop Worker",
                        "root_pid": 4321,
                        "pid": 4321,
                        "sample_id": "case-001",
                        "run_id": "run-1",
                        "expected_sha256": "0" * 64,
                        "started_at_utc": "2026-05-18T00:00:00Z",
                        "sample_path_under_case": True,
                        "execution_via": "desktop_worker",
                        "worker_pid": 111,
                        "worker_session_id": 1,
                    },
                ),
            ) as execute,
        ):
            response = client.post(
                "/cases/case-001__tencent-pc-manager/actions",
                headers=self._execution_headers(),
                json={
                    "action": "execute_uploaded_sample",
                    "sample_id": "case-001",
                    "expected_sha256": "0" * 64,
                    "run_id": "run-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["execution_state"], "running")
        self.assertEqual(data["execution_via"], "desktop_worker")
        forwarded = execute.call_args.args[0]
        self.assertEqual(forwarded["case_id"], "case-001__tencent-pc-manager")
        self.assertEqual(forwarded["sample_id"], "case-001")
        self.assertEqual(forwarded["run_id"], "run-1")
        self.assertIn("execution_lease", forwarded)
        self.assertNotIn("worker-token", response.text)
        workspace = self.workdir / "cases" / "case-001__tencent-pc-manager"
        state = json.loads((workspace / "case_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["execution"]["execution_via"], "desktop_worker")
        self.assertEqual(state["execution"]["run_id"], "run-1")

    def test_action_execute_uploaded_sample_starts_registered_file(self) -> None:
        client = self._execution_client()
        upload_headers = self._upload_headers()
        upload_headers["X-Original-Filename"] = "proof.exe"
        client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=upload_headers,
            content=b"MZ harmless placeholder",
        )

        with patch(
            "cloud_av_agent_lab.guest_agent_server.workspace.execution.subprocess.Popen"
        ) as popen:
            popen.return_value.pid = 4321
            response = client.post(
                "/cases/case-001__tencent-pc-manager/actions",
                headers=self._execution_headers(),
                json={
                    "action": "execute_uploaded_sample",
                    "sample_id": "case-001",
                    "expected_sha256": "0" * 64,
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["execution_state"], "running")
        self.assertEqual(data["root_pid"], 4321)
        args, kwargs = popen.call_args
        self.assertEqual(len(args), 1)
        self.assertEqual(len(args[0]), 1)
        self.assertTrue(args[0][0].endswith("proof.exe"))
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["cwd"].endswith("sample"))
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertIsInstance(kwargs["creationflags"], int)
        self.assertTrue(kwargs["close_fds"])
        workspace = self.workdir / "cases" / "case-001__tencent-pc-manager"
        state = json.loads((workspace / "case_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "execution_started")
        self.assertEqual(state["execution"]["pid"], 4321)
        self.assertEqual(state["execution"]["root_pid"], 4321)
        self.assertEqual(state["execution"]["state"], "running")
        self.assertEqual(state["execution"]["stored_filename"], "proof.exe")
        self.assertTrue(state["execution"]["sample_path_under_case"])
        events = _read_events(workspace / "events.jsonl")
        execution_events = [
            event for event in events if event["event_type"] == "execution_started"
        ]
        self.assertEqual(len(execution_events), 1)
        self.assertEqual(execution_events[0]["data"]["pid"], 4321)
        self.assertIn("started_at_utc", execution_events[0]["data"])

    def test_execution_status_observes_running_root_process(self) -> None:
        client = self._execution_client()
        upload_headers = self._upload_headers()
        upload_headers["X-Original-Filename"] = "proof.exe"
        client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=upload_headers,
            content=b"MZ harmless placeholder",
        )

        with patch(
            "cloud_av_agent_lab.guest_agent_server.workspace.execution.subprocess.Popen"
        ) as popen:
            popen.return_value.pid = 4321
            popen.return_value.poll.return_value = None
            start = client.post(
                "/cases/case-001__tencent-pc-manager/actions",
                headers=self._execution_headers(),
                json={
                    "action": "execute_uploaded_sample",
                    "sample_id": "case-001",
                    "expected_sha256": "0" * 64,
                },
            )
            status_response = client.get(
                "/cases/case-001__tencent-pc-manager/execution-status",
                headers=self._headers(),
            )

        self.assertEqual(start.status_code, 200)
        self.assertEqual(status_response.status_code, 200)
        data = status_response.json()["data"]
        self.assertEqual(data["execution_state"], "running")
        self.assertEqual(data["root_pid"], 4321)
        self.assertIsNone(data["exit_code"])
        workspace = self.workdir / "cases" / "case-001__tencent-pc-manager"
        events = _read_events(workspace / "events.jsonl")
        event_types = [event["event_type"] for event in events]
        self.assertIn("execution_observed", event_types)

    def test_execution_status_records_clean_exit_and_report_execution(self) -> None:
        client = self._execution_client()
        upload_headers = self._upload_headers()
        upload_headers["X-Original-Filename"] = "proof.exe"
        client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=upload_headers,
            content=b"MZ harmless placeholder",
        )

        with patch(
            "cloud_av_agent_lab.guest_agent_server.workspace.execution.subprocess.Popen"
        ) as popen:
            popen.return_value.pid = 4321
            popen.return_value.poll.return_value = 0
            client.post(
                "/cases/case-001__tencent-pc-manager/actions",
                headers=self._execution_headers(),
                json={
                    "action": "execute_uploaded_sample",
                    "sample_id": "case-001",
                    "expected_sha256": "0" * 64,
                },
            )
            status_response = client.get(
                "/cases/case-001__tencent-pc-manager/execution-status",
                headers=self._headers(),
            )

        self.assertEqual(status_response.status_code, 200)
        data = status_response.json()["data"]
        self.assertEqual(data["execution_state"], "exited_cleanly")
        self.assertEqual(data["exit_code"], 0)
        report = client.get(
            "/cases/case-001__tencent-pc-manager/report",
            headers=self._headers(),
        ).json()["data"]
        self.assertEqual(report["execution"]["state"], "exited_cleanly")
        self.assertEqual(report["execution"]["root_pid"], 4321)
        self.assertEqual(report["execution"]["exit_code"], 0)

    def test_execution_status_forwards_desktop_worker_observation(self) -> None:
        client = TestClient(
            create_app(
                workdir=self.workdir,
                token=TOKEN,
                upload_token=UPLOAD_TOKEN,
                execution_enabled=True,
                execution_token=EXECUTION_TOKEN,
                desktop_worker_enabled=True,
                desktop_worker_token="worker-token",
                app_version="test-version",
            )
        )
        client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        with patch(
            "cloud_av_agent_lab.guest_agent_server.app.DesktopWorkerClient.execution_status",
            return_value=DesktopWorkerResponse(
                status="ok",
                message="observed",
                data={
                    "case_id": "case-001__tencent-pc-manager",
                    "sample_id": "case-001",
                    "run_id": "run-1",
                    "execution_state": "exited_cleanly",
                    "root_pid": 4321,
                    "exit_code": 0,
                    "children": [],
                    "observed_at_utc": "2026-05-18T00:00:02Z",
                    "worker_execution": {
                        "state": "exited_cleanly",
                        "root_pid": 4321,
                        "exit_code": 0,
                        "sample_id": "case-001",
                        "run_id": "run-1",
                        "children": [],
                        "last_observed_at_utc": "2026-05-18T00:00:02Z",
                        "execution_via": "desktop_worker",
                    },
                },
            ),
        ):
            status_response = client.get(
                "/cases/case-001__tencent-pc-manager/execution-status",
                headers=self._headers(),
            )

        self.assertEqual(status_response.status_code, 200)
        data = status_response.json()["data"]
        self.assertEqual(data["execution_state"], "exited_cleanly")
        workspace = self.workdir / "cases" / "case-001__tencent-pc-manager"
        state = json.loads((workspace / "case_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["execution"]["execution_via"], "desktop_worker")
        self.assertEqual(state["execution"]["state"], "exited_cleanly")

    def test_execution_status_records_launch_failed(self) -> None:
        client = self._execution_client()
        upload_headers = self._upload_headers()
        upload_headers["X-Original-Filename"] = "proof.exe"
        client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=upload_headers,
            content=b"MZ harmless placeholder",
        )

        with patch(
            "cloud_av_agent_lab.guest_agent_server.workspace.execution.subprocess.Popen",
            side_effect=OSError("blocked"),
        ):
            start = client.post(
                "/cases/case-001__tencent-pc-manager/actions",
                headers=self._execution_headers(),
                json={
                    "action": "execute_uploaded_sample",
                    "sample_id": "case-001",
                    "expected_sha256": "0" * 64,
                },
            )
        status_response = client.get(
            "/cases/case-001__tencent-pc-manager/execution-status",
            headers=self._headers(),
        )

        self.assertEqual(start.status_code, 400)
        self.assertEqual(status_response.status_code, 200)
        data = status_response.json()["data"]
        self.assertEqual(data["execution_state"], "launch_failed")
        workspace = self.workdir / "cases" / "case-001__tencent-pc-manager"
        event_types = [
            event["event_type"] for event in _read_events(workspace / "events.jsonl")
        ]
        self.assertIn("execution_launch_failed", event_types)

    def test_action_execute_uploaded_sample_missing_file_is_blocked(self) -> None:
        client = self._execution_client()
        upload_headers = self._upload_headers()
        upload_headers["X-Original-Filename"] = "proof.exe"
        client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )
        upload = client.post(
            "/cases/case-001__tencent-pc-manager/sample",
            headers=upload_headers,
            content=b"MZ harmless placeholder",
        )
        sample_dir = Path(upload.json()["data"]["sample_dir"])
        (sample_dir / "proof.exe").unlink()

        with patch(
            "cloud_av_agent_lab.guest_agent_server.workspace.execution.subprocess.Popen"
        ) as popen:
            response = client.post(
                "/cases/case-001__tencent-pc-manager/actions",
                headers=self._execution_headers(),
                json={
                    "action": "execute_uploaded_sample",
                    "sample_id": "case-001",
                    "expected_sha256": "0" * 64,
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("missing before execution", response.json()["detail"])
        popen.assert_not_called()
        events = _read_events(
            self.workdir / "cases" / "case-001__tencent-pc-manager" / "events.jsonl"
        )
        event_types = [event["event_type"] for event in events]
        self.assertIn("sample_missing_before_execution", event_types)

    def test_collect_huorong_logs_writes_case_collection(self) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        _write_huorong_log_db(
            log_dir / "log.db",
            sha256="0" * 64,
            sample_path=r"C:\CloudAvAgentLab\cases\case-001__huorong\sample\eicar.txt",
        )
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )
        self.client.post(
            "/cases/case-001__huorong/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )

        with patch.object(HuorongLogCollector, "DEFAULT_LOG_DIR", log_dir):
            response = self.client.post(
                "/cases/case-001__huorong/collection/huorong",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["collection_state"], "collected")
        self.assertEqual(data["verdict"], "intercepted")
        self.assertTrue(data["intercepted"])
        self.assertEqual(data["evidence_count"], 1)
        self.assertEqual(data["events"][0]["event_type"], "av_quarantined")
        self.assertEqual(data["timeline"][-1]["source"], "product_log")
        workspace = self.workdir / "cases" / "case-001__huorong"
        collection_file = workspace / "case_collection.json"
        self.assertTrue(collection_file.is_file())
        saved = json.loads(collection_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["product_id"], "huorong")
        artifacts = saved["artifacts"]
        self.assertEqual(artifacts["schema_version"], "collector-artifacts.v1")
        item_by_path = {item["path"]: item for item in artifacts["items"]}
        self.assertEqual(
            item_by_path["collection/huorong/log.db"]["category"],
            "raw_product_log",
        )
        self.assertFalse(
            item_by_path["collection/huorong/log.db"]["include_in_evidence"]
        )
        self.assertEqual(
            item_by_path["collection/huorong/log.db"]["redaction_state"],
            "raw_blocked",
        )
        self.assertTrue(
            item_by_path["collector/normalized_evidence.json"]["include_in_evidence"]
        )
        self.assertTrue((workspace / "collection" / "huorong" / "log.db").is_file())
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn(UPLOAD_TOKEN, response.text)

    def test_collect_huorong_logs_discovers_rotated_table_name(self) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        _write_huorong_log_db(
            log_dir / "log.db",
            sha256="0" * 64,
            table_name="HrLogV3_61",
        )
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )
        self.client.post(
            "/cases/case-001__huorong/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )

        with patch.object(HuorongLogCollector, "DEFAULT_LOG_DIR", log_dir):
            response = self.client.post(
                "/cases/case-001__huorong/collection/huorong",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["collection_state"], "collected")
        self.assertEqual(data["verdict"], "intercepted")
        self.assertEqual(data["evidence_count"], 1)

    def test_collect_huorong_logs_accepts_millisecond_timestamps(self) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        _write_huorong_log_db(
            log_dir / "log.db",
            sha256="0" * 64,
            timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
        )
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )
        self.client.post(
            "/cases/case-001__huorong/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )

        with patch.object(HuorongLogCollector, "DEFAULT_LOG_DIR", log_dir):
            response = self.client.post(
                "/cases/case-001__huorong/collection/huorong",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["collection_state"], "collected")
        self.assertEqual(data["verdict"], "intercepted")
        self.assertEqual(data["evidence_count"], 1)

    def test_collect_huorong_logs_reads_json_from_fifth_column(self) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        _write_huorong_log_db(
            log_dir / "log.db",
            sha256="0" * 64,
            timestamp_column="timestamp",
            json_column="payload",
        )
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )
        self.client.post(
            "/cases/case-001__huorong/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )

        with patch.object(HuorongLogCollector, "DEFAULT_LOG_DIR", log_dir):
            response = self.client.post(
                "/cases/case-001__huorong/collection/huorong",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["collection_state"], "collected")
        self.assertEqual(data["verdict"], "intercepted")
        self.assertEqual(data["evidence_count"], 1)
        self.assertEqual(data["events"][0]["evidence"]["json_column"], "payload")

    def test_collect_huorong_logs_reads_detail_column_with_nested_detail(self) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        _write_huorong_log_db(
            log_dir / "log.db",
            sha256="0" * 64,
            timestamp_column="timestamp",
            json_column="detail",
        )
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )
        self.client.post(
            "/cases/case-001__huorong/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )

        with patch.object(HuorongLogCollector, "DEFAULT_LOG_DIR", log_dir):
            response = self.client.post(
                "/cases/case-001__huorong/collection/huorong",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["collection_state"], "collected")
        self.assertEqual(data["verdict"], "intercepted")
        self.assertEqual(data["evidence_count"], 1)
        self.assertEqual(data["events"][0]["evidence"]["json_column"], "detail")
        self.assertEqual(
            data["events"][0]["evidence"]["detail"]["recname"],
            "TEST/AVEngTestFile!EICAR",
        )

    def test_collect_huorong_logs_returns_available_tables_on_missing_table(
        self,
    ) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        _write_huorong_log_db(
            log_dir / "log.db",
            sha256="0" * 64,
            table_name="OtherLogTable",
        )
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )

        with patch.object(HuorongLogCollector, "DEFAULT_LOG_DIR", log_dir):
            response = self.client.post(
                "/cases/case-001__huorong/collection/huorong",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["collection_state"], "failed")
        self.assertEqual(data["verdict"], "unknown")
        self.assertIn("available_tables", data["errors"][0])
        self.assertIn("OtherLogTable", data["errors"][0])

    def test_collection_status_reads_case_collection_without_sample_content(
        self,
    ) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        _write_huorong_log_db(log_dir / "log.db", sha256="0" * 64)
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )
        self.client.post(
            "/cases/case-001__huorong/sample",
            headers=self._upload_headers(),
            content=b"DO-NOT-READ-SAMPLE-CONTENT",
        )
        with patch.object(HuorongLogCollector, "DEFAULT_LOG_DIR", log_dir):
            self.client.post(
                "/cases/case-001__huorong/collection/huorong",
                headers=self._headers(),
            )

        response = self.client.get(
            "/cases/case-001__huorong/collection/status",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["collection_state"], "collected")
        self.assertEqual(data["verdict"], "intercepted")
        self.assertNotIn("DO-NOT-READ-SAMPLE-CONTENT", response.text)
        self.assertNotIn(TOKEN, response.text)

    def test_collection_missing_case_returns_404(self) -> None:
        response = self.client.post(
            "/cases/missing-case/collection/huorong",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("guest-prepare-case", response.json()["detail"])

    def test_security_product_readiness_huorong_writes_case_metadata(self) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        (log_dir / "log.db").write_bytes(b"sqlite-placeholder")
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )

        with patch(
            "cloud_av_agent_lab.guest_agent_server."
            "security_product_readiness.huorong."
            "HuorongSecurityProductReadinessProbe.DEFAULT_LOG_DIR",
            log_dir,
        ):
            response = self.client.post(
                "/cases/case-001__huorong/security-product-readiness/huorong",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["state"], "ready")
        self.assertEqual(data["scope"], "log_observability")
        self.assertEqual(data["protection_state"], "unknown")
        workspace = self.workdir / "cases" / "case-001__huorong"
        self.assertTrue((workspace / "case_security_product_readiness.json").is_file())
        self.assertTrue(
            (workspace / "security-product-readiness" / "huorong" / "log.db").is_file()
        )
        state = json.loads((workspace / "case_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["security_product_readiness"]["state"], "ready")
        report = json.loads(
            (workspace / "case_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["security_product_readiness"]["state"], "ready")
        events = _read_events(workspace / "events.jsonl")
        event_types = [event["event_type"] for event in events]
        self.assertIn("security_product_readiness_started", event_types)
        self.assertIn("security_product_readiness_checked", event_types)
        self.assertNotIn(TOKEN, response.text)

    def test_security_product_readiness_status_returns_latest_result(self) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        (log_dir / "log.db").write_bytes(b"sqlite-placeholder")
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )
        with patch(
            "cloud_av_agent_lab.guest_agent_server."
            "security_product_readiness.huorong."
            "HuorongSecurityProductReadinessProbe.DEFAULT_LOG_DIR",
            log_dir,
        ):
            self.client.post(
                "/cases/case-001__huorong/security-product-readiness/huorong",
                headers=self._headers(),
            )

        response = self.client.get(
            "/cases/case-001__huorong/security-product-readiness/status",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["state"], "ready")
        self.assertIn("recent_events", data)

    def test_security_product_readiness_missing_case_returns_404(self) -> None:
        response = self.client.post(
            "/cases/missing-case/security-product-readiness/huorong",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("guest-prepare-case", response.json()["detail"])

    def test_security_product_readiness_unsupported_product_returns_200(self) -> None:
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=_prepare_payload(),
        )

        response = self.client.post(
            "/cases/case-001__tencent-pc-manager/"
            "security-product-readiness/unsupported",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["state"], "unsupported")
        self.assertNotIn(TOKEN, response.text)

    def test_security_product_readiness_rejects_path_traversal_case_id(self) -> None:
        response = self.client.post(
            "/cases/..%2Fescape/security-product-readiness/huorong",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.workdir.parent / "escape").exists())

    def test_collection_rejects_path_traversal_case_id(self) -> None:
        response = self.client.post(
            "/cases/..%2Fescape/collection/huorong",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.workdir.parent / "escape").exists())

    def test_case_summary_success_generates_conservative_verdict(self) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        _write_huorong_log_db(log_dir / "log.db", sha256="0" * 64)
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )
        self.client.post(
            "/cases/case-001__huorong/sample",
            headers=self._upload_headers(),
            content=b"EICAR harmless placeholder",
        )
        with patch.object(HuorongLogCollector, "DEFAULT_LOG_DIR", log_dir):
            self.client.post(
                "/cases/case-001__huorong/collection/huorong",
                headers=self._headers(),
            )

        response = self.client.get(
            "/cases/case-001__huorong/summary",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        summary = payload["data"]
        self.assertEqual(payload["message"], "case summary loaded")
        self.assertEqual(summary["case_id"], "case-001__huorong")
        self.assertEqual(summary["verdict"], "detected_or_blocked")
        self.assertEqual(summary["confidence"], "high")
        self.assertGreaterEqual(len(summary["reasons"]), 1)
        workspace = self.workdir / "cases" / "case-001__huorong"
        self.assertTrue((workspace / "case_summary.json").is_file())
        self.assertTrue((workspace / "case_summary.md").is_file())
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn("EICAR harmless placeholder", response.text)

    def test_case_summary_missing_case_returns_404(self) -> None:
        response = self.client.get(
            "/cases/missing-case/summary",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("guest-prepare-case", response.json()["detail"])

    def test_evidence_bundle_success_excludes_sample_and_has_manifest_hashes(
        self,
    ) -> None:
        payload = _prepare_huorong_payload()
        log_dir = self.workdir / "huorong-source"
        log_dir.mkdir()
        _write_huorong_log_db(log_dir / "log.db", sha256="0" * 64)
        self.client.post(
            "/prepare-case",
            headers=self._headers(),
            json=payload,
        )
        self.client.post(
            "/cases/case-001__huorong/sample",
            headers=self._upload_headers(),
            content=b"DO-NOT-READ-SAMPLE-CONTENT",
        )
        with patch.object(HuorongLogCollector, "DEFAULT_LOG_DIR", log_dir):
            self.client.post(
                "/cases/case-001__huorong/collection/huorong",
                headers=self._headers(),
            )

        response = self.client.get(
            "/cases/case-001__huorong/evidence-bundle",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        bundle_path = self.workdir / "bundle.zip"
        bundle_path.write_bytes(response.content)
        with zipfile.ZipFile(bundle_path) as bundle:
            names = set(bundle.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("case_state.json", names)
            self.assertIn("case_report.json", names)
            self.assertIn("case_collection.json", names)
            self.assertIn("case_summary.json", names)
            self.assertIn("events.jsonl", names)
            self.assertIn("sample/sample.json", names)
            self.assertIn("collector/normalized_evidence.json", names)
            self.assertNotIn("collection/huorong/log.db", names)
            self.assertNotIn("sample/eicar.txt", names)
            self.assertNotIn("configs/real.toml", names)
            manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["schema_version"], "evidence-bundle.v2")
            self.assertEqual(manifest["trust_model"], "dirty_instance_untrusted")
            self.assertFalse(manifest["forensic_grade"])
            self.assertFalse(manifest["raw_binary_included"])
            self.assertTrue(
                any(
                    item["path"] == "collection/huorong/log.db"
                    and item["reason"] == "raw_binary_redaction_not_supported"
                    for item in manifest["excluded_path_details"]
                )
            )
            for item in manifest["files"]:
                content = bundle.read(item["path"])
                self.assertEqual(item["sha256"], hashlib.sha256(content).hexdigest())
            bundle_text = "\n".join(
                bundle.read(name).decode("utf-8", errors="ignore")
                for name in names
                if name.endswith((".json", ".jsonl", ".md")) and name != "manifest.json"
            )
        self.assertNotIn("DO-NOT-READ-SAMPLE-CONTENT", bundle_text)
        self.assertNotIn(TOKEN, bundle_text)
        self.assertNotIn(UPLOAD_TOKEN, bundle_text)
        self.assertFalse(any(name.casefold().endswith("real.toml") for name in names))

    def test_evidence_bundle_missing_case_returns_404(self) -> None:
        response = self.client.get(
            "/cases/missing-case/evidence-bundle",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("guest-prepare-case", response.json()["detail"])


def _prepare_payload() -> dict[str, object]:
    return {
        "case": {"id": "case-001__tencent-pc-manager"},
        "sample": {
            "id": "case-001",
            "sha256": "0" * 64,
            "category": "persistence",
            "cloud_object_uri": "cos://bucket/redacted/case-001.bin",
            "expected_behaviors": ["persistence"],
            "notes": "metadata only",
        },
        "vm": {
            "id": "sg-win10",
            "provider": "tencent-cloud-lighthouse",
            "region": "ap-singapore",
            "instance_id": "lhins-example",
            "baseline_snapshot": "snap-example",
            "network_profile": "isolated-egress-deny-by-default",
            "product_id": "tencent-pc-manager",
        },
        "product": {
            "id": "tencent-pc-manager",
            "display_name": "Tencent PC Manager",
            "vendor": "Tencent",
        },
    }


def _prepare_huorong_payload() -> dict[str, object]:
    payload = _prepare_payload()
    payload["case"] = {"id": "case-001__huorong"}
    payload["vm"] = {
        **payload["vm"],
        "id": "sg-win10-huorong",
        "product_id": "huorong",
    }
    payload["product"] = {
        "id": "huorong",
        "display_name": "Huorong Internet Security",
        "vendor": "Huorong",
    }
    return payload


def _write_huorong_log_db(
    path: Path,
    sha256: str,
    sample_path: str = r"C:\CloudAvAgentLab\cases\case-001__huorong\sample\eicar.txt",
    table_name: str = "HrLogV3_60",
    timestamp: int | None = None,
    timestamp_column: str = "ts",
    json_column: str = "raw_json",
) -> None:
    db = sqlite3.connect(path)
    try:
        db.execute(
            f"CREATE TABLE {table_name} ("
            f"id INTEGER, fid INTEGER, fname TEXT, {timestamp_column} INTEGER, "
            f"{json_column} TEXT, guid INTEGER"
            ")"
        )
        raw_json = {
            "guid": 1,
            "fid": 60,
            "detail": {
                "recname": "TEST/AVEngTestFile!EICAR",
                "description": "EICAR test string detected",
                "risk": "high",
                "action": "quarantine",
                "treatment": "quarantine",
                "result": "success",
                "pathname": sample_path,
                "sha256": sha256,
            },
            "version": {"product": "Huorong", "dbtime": "test"},
        }
        db.execute(
            f"INSERT INTO {table_name} (id, fid, fname, {timestamp_column}, "
            f"{json_column}, guid) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                1,
                60,
                "filemon",
                timestamp or int(datetime.now(timezone.utc).timestamp()),
                json.dumps(raw_json),
                1,
            ),
        )
        db.commit()
    finally:
        db.close()


def _read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
