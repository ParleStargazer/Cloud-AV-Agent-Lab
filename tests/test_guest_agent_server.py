from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from fastapi.testclient import TestClient

    from cloud_av_agent_lab.guest_agent_server.app import create_app
    from cloud_av_agent_lab.guest_agent_server.workspace import FileProbe
except ModuleNotFoundError:  # pragma: no cover - optional dependency absent
    TestClient = None
    create_app = None
    FileProbe = None


TOKEN = "unit-test-token"
UPLOAD_TOKEN = "unit-test-upload-token"


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
        self.assertEqual(files, ["case.json", "case_state.json", "events.jsonl"])
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
            "cloud_av_agent_lab.guest_agent_server.workspace._probe_sample_current_status",
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
            "cloud_av_agent_lab.guest_agent_server.workspace._probe_sample_current_status",
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
            "cloud_av_agent_lab.guest_agent_server.workspace._probe_sample_current_status"
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


def _read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
