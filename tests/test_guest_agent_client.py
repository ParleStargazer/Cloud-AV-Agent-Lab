from __future__ import annotations

import json
import sys
import tempfile
import zipfile
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
from cloud_av_agent_lab.core.contracts import (
    GuestAgentConfig,
    GuestAgentDesktopWorkerConfig,
    GuestAgentExecutionConfig,
)
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

    def request_binary(self, **kwargs: object) -> NetworkResponse:
        self.calls.append(kwargs)
        return self.response


class FailingNetworkClient(FakeNetworkClient):
    def request_bytes(self, **kwargs: object) -> NetworkResponse:
        self.calls.append(kwargs)
        raise RuntimeError("secret upload-token should not leak")


class ConnectionFailingNetworkClient(FakeNetworkClient):
    def request_json(self, **kwargs: object) -> NetworkResponse:
        self.calls.append(kwargs)
        raise ConnectionError("target refused connection")


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
        self.assertEqual(payload["sample"]["md5"], case.sample.md5)
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

    def test_worker_status_uses_network_client_when_enabled(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
            timeout_seconds=5,
            desktop_worker=GuestAgentDesktopWorkerConfig(
                enabled=True,
                timeout_seconds=2,
            ),
        )
        network = FakeNetworkClient(
            NetworkResponse(
                status=200,
                headers={},
                body=(
                    b'{"status":"ok","message":"desktop worker status loaded",'
                    b'"data":{"desktop_worker_ready":true,'
                    b'"worker_session_id":1}}'
                ),
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.worker_status()

        self.assertTrue(response.data["desktop_worker_ready"])
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], "http://guest-agent.local:8080/worker/status")
        self.assertEqual(call["timeout_seconds"], 2)
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_worker_status_requires_local_desktop_worker_config(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
        )
        client = GuestAgentClient(
            config, network=FakeNetworkClient(), env={"GUEST_TOKEN": "secret"}
        )

        with self.assertRaises(GuestAgentError) as error:
            client.worker_status()

        self.assertEqual(error.exception.source, "local")
        self.assertIn("desktop worker", str(error.exception).casefold())

    def test_case_report_uses_network_client(self) -> None:
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
                body=b'{"status":"ok","message":"case report loaded","data":{"case_id":"case-001__tencent-pc-manager","upload_state":"stable"}}',
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.case_report("case-001__tencent-pc-manager")

        self.assertEqual(response.data["upload_state"], "stable")
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/case-001__tencent-pc-manager/report",
        )
        self.assertEqual(call["timeout_seconds"], 5)
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_case_summary_uses_network_client(self) -> None:
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
                body=b'{"status":"ok","message":"case summary loaded","data":{"case_id":"case-001__huorong","verdict":"detected_or_blocked"}}',
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.case_summary("case-001__huorong")

        self.assertEqual(response.data["verdict"], "detected_or_blocked")
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/case-001__huorong/summary",
        )
        self.assertEqual(call["timeout_seconds"], 5)
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_export_evidence_bundle_uses_network_client(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
            timeout_seconds=5,
        )
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as bundle:
            bundle.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "trust_model": "dirty_instance_untrusted",
                        "forensic_grade": False,
                        "raw_binary_included": False,
                    }
                ),
            )
        bundle_bytes = zip_buffer.getvalue()
        network = FakeNetworkClient(
            NetworkResponse(
                status=200,
                headers={"content-type": "application/zip"},
                body=bundle_bytes,
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "bundle.zip"
            response = client.export_evidence_bundle(
                "case-001__huorong",
                output_path,
                timeout_seconds=120,
            )

            self.assertEqual(output_path.read_bytes(), bundle_bytes)

        self.assertEqual(response.message, "evidence bundle saved")
        self.assertEqual(response.data["size"], len(bundle_bytes))
        self.assertEqual(response.data["trust_model"], "dirty_instance_untrusted")
        self.assertFalse(response.data["forensic_grade"])
        self.assertFalse(response.data["raw_binary_included"])
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/case-001__huorong/evidence-bundle",
        )
        self.assertEqual(call["timeout_seconds"], 120)
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_collect_logs_uses_network_client(self) -> None:
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
                body=b'{"status":"ok","message":"collection completed","data":{"product_id":"huorong","verdict":"intercepted"}}',
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.collect_logs("case-001__huorong", "huorong")

        self.assertEqual(response.data["verdict"], "intercepted")
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/case-001__huorong/collection/huorong",
        )
        self.assertEqual(call["timeout_seconds"], 5)
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_collection_status_uses_network_client(self) -> None:
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
                body=b'{"status":"ok","message":"collection status loaded","data":{"collection_state":"collected"}}',
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.collection_status("case-001__huorong")

        self.assertEqual(response.data["collection_state"], "collected")
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/case-001__huorong/collection/status",
        )
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_check_security_product_readiness_uses_network_client(self) -> None:
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
                body=(
                    b'{"status":"ok","message":"security product readiness '
                    b'checked","data":{"state":"ready","confidence":"medium"}}'
                ),
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.check_security_product_readiness(
            "case-001__huorong",
            "huorong",
        )

        self.assertEqual(response.data["state"], "ready")
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/"
            "case-001__huorong/security-product-readiness/huorong",
        )
        self.assertEqual(call["timeout_seconds"], 5)
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_security_product_readiness_status_404_has_clear_prepare_case_hint(
        self,
    ) -> None:
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
            client.security_product_readiness_status("missing-case")

        message = str(error.exception)
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("HTTP 404 Not Found", message)
        self.assertIn("guest-prepare-case", message)
        self.assertNotIn("agent-secret", message)

    def test_security_product_readiness_status_passes_product_query(self) -> None:
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
                body=(
                    b'{"status":"ok","message":"security product readiness '
                    b'status loaded","data":{"state":"ready"}}'
                ),
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.security_product_readiness_status(
            "case-001__windows-defender",
            product_id="windows-defender",
        )

        self.assertEqual(response.data["state"], "ready")
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/"
            "case-001__windows-defender/security-product-readiness/status"
            "?product_id=windows-defender",
        )
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_execution_status_uses_network_client(self) -> None:
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
                body=b'{"status":"ok","message":"execution status observed","data":{"execution_state":"running","root_pid":1234}}',
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "secret"}
        )

        response = client.execution_status("case-001__tencent-pc-manager")

        self.assertEqual(response.data["execution_state"], "running")
        self.assertEqual(response.data["root_pid"], 1234)
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/case-001__tencent-pc-manager/execution-status",
        )
        self.assertEqual(call["timeout_seconds"], 5)
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_execution_status_404_has_clear_prepare_case_hint(self) -> None:
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
            client.execution_status("missing-case")

        message = str(error.exception)
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("HTTP 404 Not Found", message)
        self.assertIn("guest-prepare-case", message)
        self.assertNotIn("agent-secret", message)

    def test_case_report_404_has_clear_prepare_case_hint(self) -> None:
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
            client.case_report("missing-case")

        message = str(error.exception)
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("HTTP 404 Not Found", message)
        self.assertIn("guest-prepare-case", message)
        self.assertNotIn("agent-secret", message)

    def test_case_summary_404_has_clear_prepare_case_hint(self) -> None:
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
            client.case_summary("missing-case")

        message = str(error.exception)
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("HTTP 404 Not Found", message)
        self.assertIn("guest-prepare-case", message)
        self.assertNotIn("agent-secret", message)

    def test_export_evidence_bundle_404_has_clear_prepare_case_hint(self) -> None:
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

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(GuestAgentError) as error:
                client.export_evidence_bundle("missing-case", Path(tmp) / "bundle.zip")

        message = str(error.exception)
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("HTTP 404 Not Found", message)
        self.assertIn("guest-prepare-case", message)
        self.assertNotIn("agent-secret", message)

    def test_collect_logs_404_has_clear_prepare_case_hint(self) -> None:
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
            client.collect_logs("missing-case", "huorong")

        message = str(error.exception)
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("HTTP 404 Not Found", message)
        self.assertIn("guest-prepare-case", message)
        self.assertNotIn("agent-secret", message)

    def test_execute_uploaded_sample_defaults_to_dry_run_action(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
            execution=GuestAgentExecutionConfig(timeout_seconds=7),
        )
        network = FakeNetworkClient(
            NetworkResponse(
                status=200,
                headers={},
                body=b'{"status":"ok","message":"dry-run checked uploaded sample metadata; no sample was executed","data":{"execution_state":"execution_dry_run_checked"}}',
            )
        )
        client = GuestAgentClient(
            config, network=network, env={"GUEST_TOKEN": "agent-secret"}
        )

        response = client.execute_uploaded_sample(
            case_id="case-001__tencent-pc-manager",
            sample_id="case-001",
            expected_sha256="0" * 64,
        )

        self.assertEqual(response.data["execution_state"], "execution_dry_run_checked")
        self.assertEqual(len(network.calls), 1)
        call = network.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["url"],
            "http://guest-agent.local:8080/cases/case-001__tencent-pc-manager/actions",
        )
        self.assertEqual(call["timeout_seconds"], 7)
        self.assertEqual(
            call["payload"],
            {
                "action": "dry_run_execute_uploaded_sample",
                "sample_id": "case-001",
                "expected_sha256": "0" * 64,
            },
        )
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer agent-secret")
        self.assertNotIn("X-Execution-Token", headers)

    def test_case_action_injects_execution_token_when_enabled(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
            execution=GuestAgentExecutionConfig(
                enabled=True,
                token_env="EXEC_TOKEN",
                timeout_seconds=9,
            ),
        )
        network = FakeNetworkClient(
            NetworkResponse(
                status=200,
                headers={},
                body=b'{"status":"ok","message":"execution is disabled; no sample was executed","data":{"execution_state":"execution_disabled"}}',
            )
        )
        client = GuestAgentClient(
            config,
            network=network,
            env={"GUEST_TOKEN": "agent-secret", "EXEC_TOKEN": "execution-secret"},
        )

        response = client.execute_uploaded_sample(
            case_id="case-001__tencent-pc-manager",
            sample_id="case-001",
            dry_run=False,
            handler_id="batch_script",
        )

        self.assertEqual(response.data["execution_state"], "execution_disabled")
        call = network.calls[0]
        self.assertEqual(call["timeout_seconds"], 9)
        self.assertEqual(call["payload"]["action"], "execute_uploaded_sample")
        self.assertEqual(call["payload"]["handler_id"], "batch_script")
        headers = call["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer agent-secret")
        self.assertEqual(headers["X-Execution-Token"], "execution-secret")

    def test_case_action_requires_execution_token_when_enabled(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
            execution=GuestAgentExecutionConfig(
                enabled=True,
                token_env="EXEC_TOKEN",
            ),
        )
        client = GuestAgentClient(
            config, network=FakeNetworkClient(), env={"GUEST_TOKEN": "agent-secret"}
        )

        with self.assertRaises(GuestAgentError) as error:
            client.execute_uploaded_sample(
                case_id="case-001__tencent-pc-manager",
                sample_id="case-001",
                dry_run=False,
            )

        message = str(error.exception)
        self.assertIn("EXEC_TOKEN", message)
        self.assertNotIn("agent-secret", message)

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
        self.assertEqual(error.exception.source, "remote")

    def test_connection_error_is_tagged_as_network_error(self) -> None:
        config = GuestAgentConfig(
            enabled=True,
            base_url="http://guest-agent.local:8080",
            token_env="GUEST_TOKEN",
        )
        client = GuestAgentClient(
            config,
            network=ConnectionFailingNetworkClient(),
            env={"GUEST_TOKEN": "secret"},
        )

        with self.assertRaises(GuestAgentError) as error:
            client.health()

        message = str(error.exception)
        self.assertEqual(error.exception.source, "network")
        self.assertIn("无法连接到 Guest Agent", message)
        self.assertIn("ConnectionError", message)
        self.assertNotIn("secret", message)

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
                md5="1" * 32,
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
        self.assertEqual(headers["X-Sample-Md5"], "1" * 32)
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
