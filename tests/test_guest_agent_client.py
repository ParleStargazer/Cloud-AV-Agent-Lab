from __future__ import annotations

import json
import sys
import tempfile
from io import BytesIO
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.adapters.guest_agent_client import (
    GuestAgentClient,
    GuestAgentError,
)
from cloud_av_agent_lab.config import load_config
from cloud_av_agent_lab.core.contracts import GuestAgentConfig
from cloud_av_agent_lab.core.pipeline import TestPipeline
from cloud_av_agent_lab.network.client import NetworkResponse


class FakeNetworkClient:
    def __init__(self, response: NetworkResponse | None = None) -> None:
        self.response = response or NetworkResponse(
            status=200,
            headers={},
            body=b'{"status":"ok","message":"ready","data":{"agent":"mvp"}}',
        )
        self.proxy_map: dict[str, str] = {}
        self.calls: list[dict[str, object]] = []

    def request_json(self, **kwargs: object) -> NetworkResponse:
        self.calls.append(kwargs)
        return self.response

    def request_bytes(self, **kwargs: object) -> NetworkResponse:
        self.calls.append(kwargs)
        return self.response


class FailingNetworkClient(FakeNetworkClient):
    def request_bytes(self, **kwargs: object) -> NetworkResponse:
        self.calls.append(kwargs)
        raise RuntimeError("secret upload-token should not leak")


class HttpErrorNetworkClient(FakeNetworkClient):
    def request_bytes(self, **kwargs: object) -> NetworkResponse:
        self.calls.append(kwargs)
        raise HTTPError(
            url=str(kwargs["url"]),
            code=404,
            msg="Not Found",
            hdrs={},
            fp=BytesIO(
                b'{"detail":"case workspace does not exist; run guest-prepare-case first"}'
            ),
        )


class GuestAgentClientTests(TestCase):
    def test_health_uses_network_client_and_token_header(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
            timeout_seconds=3,
        )
        network = FakeNetworkClient()
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.health()

        self.assertEqual(response.status, "ok")
        self.assertEqual(response.data["agent"], "mvp")
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], "http://guest-agent.local:8080/health")
        self.assertEqual(call["timeout_seconds"], 3)
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_enabled_guest_agent_requires_token_environment_variable(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
        )

        with self.assertRaises(GuestAgentError) as error:
            GuestAgentClient(config, network=FakeNetworkClient(), env={})

        self.assertIn("GUEST_TOKEN", str(error.exception))

    def test_prepare_case_payload_uses_cloud_uri_and_no_local_sample_path(self) -> None:
        lab_config = load_config(ROOT / "configs" / "lab.example.toml")
        case = TestPipeline(lab_config).build_plan()[0]
        config = replace(
            lab_config.guest_agent,
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
        )
        network = FakeNetworkClient(
            NetworkResponse(
                status=200,
                headers={},
                body=b'{"status":"ok","message":"prepared","data":{"workspace":"C:\\\\cases\\\\case-001"}}',
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.prepare_case(case)

        self.assertEqual(response.message, "prepared")
        payload = network.calls[0]["payload"]
        self.assertIsInstance(payload, dict)
        payload_text = json.dumps(payload, ensure_ascii=False)
        self.assertIn(case.sample.cloud_object_uri, payload_text)
        self.assertNotIn("local_path", payload_text)
        self.assertNotIn("C:\\samples", payload_text)
        self.assertNotIn("/samples/", payload_text)
        self.assertEqual(
            payload["sample"]["cloud_object_uri"], case.sample.cloud_object_uri
        )
        self.assertEqual(
            network.calls[0]["url"], "http://guest-agent.local:8080/prepare-case"
        )

    def test_case_status_uses_network_client(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
            timeout_seconds=5,
        )
        network = FakeNetworkClient(
            NetworkResponse(
                status=200,
                headers={},
                body=b'{"status":"ok","message":"case status loaded","data":{"state":{"upload_state":"stable"}}}',
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.case_status("case-001__tencent-pc-manager")

        self.assertEqual(response.data["state"]["upload_state"], "stable")
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/case-001__tencent-pc-manager/status",
        )
        self.assertEqual(call["timeout_seconds"], 5)
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_case_status_404_has_clear_prepare_case_hint(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
        )
        network = FakeNetworkClient(
            NetworkResponse(
                status=404,
                headers={},
                body=b'{"detail":"case workspace does not exist; run guest-prepare-case first"}',
                reason="Not Found",
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "agent-secret"}
        )

        with self.assertRaises(GuestAgentError) as error:
            client.case_status("missing-case")

        message = str(error.exception)
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("HTTP 404 Not Found", message)
        self.assertIn("guest-prepare-case", message)
        self.assertNotIn("agent-secret", message)

    def test_http_error_status_raises_guest_agent_error(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
        )
        network = FakeNetworkClient(
            NetworkResponse(status=500, headers={}, body=b'{"message":"boom"}')
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        with self.assertRaises(GuestAgentError) as error:
            client.system_info()

        self.assertIn("HTTP 500", str(error.exception))
        self.assertIn("boom", str(error.exception))

    def test_http_error_detail_raises_guest_agent_error(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
        )
        network = FakeNetworkClient(
            NetworkResponse(
                status=404,
                headers={},
                body=b'{"detail":"case workspace does not exist; run guest-prepare-case first"}',
                reason="Not Found",
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        with self.assertRaises(GuestAgentError) as error:
            client.system_info()

        message = str(error.exception)
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("HTTP 404 Not Found", message)
        self.assertIn("guest-prepare-case", message)

    def test_upload_sample_injects_auth_and_upload_tokens(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
            timeout_seconds=4,
        )
        network = FakeNetworkClient(
            NetworkResponse(
                status=200,
                headers={},
                body=b'{"status":"ok","message":"sample uploaded","data":{"workspace":"C:\\\\cases\\\\case-001","upload_state":"removed_after_save","removed_after_save":true}}',
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "eicar.txt"
            file_path.write_bytes(b"EICAR harmless placeholder")
            client = GuestAgentClient(
                config,
                network=network,
                env={
                    "GUEST_TOKEN": "agent-secret",
                    "CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN": "upload-secret",
                },
            )

            response = client.upload_sample(
                case_id="case-001__tencent-pc-manager",
                sample_id="case-001",
                file_path=file_path,
                sha256="0" * 64,
            )

        self.assertEqual(response.message, "sample uploaded")
        self.assertEqual(response.data["upload_state"], "removed_after_save")
        self.assertTrue(response.data["removed_after_save"])
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/case-001__tencent-pc-manager/sample",
        )
        self.assertEqual(call["body"], b"EICAR harmless placeholder")
        self.assertEqual(call["timeout_seconds"], 4)
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer agent-secret")
        self.assertEqual(headers["X-Upload-Token"], "upload-secret")
        self.assertEqual(headers["X-Sample-Id"], "case-001")
        self.assertEqual(headers["X-Sample-Sha256"], "0" * 64)
        self.assertEqual(headers["X-Original-Filename"], "eicar.txt")

    def test_upload_sample_error_does_not_leak_tokens_or_path(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
        )
        network = FailingNetworkClient()

        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "eicar.txt"
            file_path.write_bytes(b"EICAR harmless placeholder")
            client = GuestAgentClient(
                config,
                network=network,
                env={
                    "GUEST_TOKEN": "agent-secret",
                    "CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN": "upload-secret",
                },
            )
            with self.assertRaises(GuestAgentError) as error:
                client.upload_sample(
                    case_id="case-001",
                    sample_id="case-001",
                    file_path=file_path,
                )

        message = str(error.exception)
        self.assertNotIn("agent-secret", message)
        self.assertNotIn("upload-secret", message)
        self.assertNotIn("eicar.txt", message)

    def test_upload_sample_http_error_includes_status_and_detail(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
        )
        network = HttpErrorNetworkClient()

        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "eicar.txt"
            file_path.write_bytes(b"EICAR harmless placeholder")
            client = GuestAgentClient(
                config,
                network=network,
                env={
                    "GUEST_TOKEN": "agent-secret",
                    "CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN": "upload-secret",
                },
            )
            with self.assertRaises(GuestAgentError) as error:
                client.upload_sample(
                    case_id="missing-case",
                    sample_id="case-001",
                    file_path=file_path,
                )

        message = str(error.exception)
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("HTTP 404 Not Found", message)
        self.assertIn("guest-prepare-case", message)
        self.assertNotIn("agent-secret", message)
        self.assertNotIn("upload-secret", message)
        self.assertNotIn("eicar.txt", message)
