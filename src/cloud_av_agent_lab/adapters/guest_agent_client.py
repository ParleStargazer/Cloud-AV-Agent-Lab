from __future__ import annotations

import json
import os
import hashlib
import io
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urljoin

from cloud_av_agent_lab.core.contracts import GuestAgentConfig, TestCase
from cloud_av_agent_lab.network.client import NetworkClient, NetworkResponse

UPLOAD_TOKEN_ENV = "CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN"
EXECUTION_TOKEN_ENV = "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"


class GuestAgentError(RuntimeError):
    """Raised when Guest Agent configuration or HTTP communication fails."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        source: str = "remote",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.source = source


@dataclass(frozen=True)
class GuestAgentResponse:
    status: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class GuestAgentClient:
    """HTTP client for the cloud-side Guest Agent MVP.

    All outbound access goes through NetworkClient so the temporary development
    proxy remains isolated to the network package.
    """

    def __init__(
        self,
        config: GuestAgentConfig,
        network: NetworkClient | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/") + "/"
        self.network = network or NetworkClient()
        self.env = env if env is not None else os.environ
        self.token = self._load_token()

    def health(self, timeout_seconds: float | None = None) -> GuestAgentResponse:
        return self._request("health", method="GET", timeout_seconds=timeout_seconds)

    def system_info(self) -> GuestAgentResponse:
        return self._request("system-info", method="GET")

    def worker_status(
        self,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        if not self.config.desktop_worker.enabled:
            raise GuestAgentError(
                "Guest Agent desktop worker integration is disabled in config",
                source="local",
            )
        return self._request(
            "worker/status",
            method="GET",
            timeout_seconds=(
                self.config.desktop_worker.timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
        )

    def prepare_case(
        self,
        case: TestCase,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        return self._request(
            "prepare-case",
            method="POST",
            payload=_prepare_case_payload(case),
            timeout_seconds=timeout_seconds,
        )

    def case_status(
        self,
        case_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        return self._request(
            f"cases/{quote(case_id, safe='')}/status",
            method="GET",
            timeout_seconds=timeout_seconds,
        )

    def case_report(self, case_id: str) -> GuestAgentResponse:
        return self._request(
            f"cases/{quote(case_id, safe='')}/report",
            method="GET",
        )

    def case_summary(
        self,
        case_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        return self._request(
            f"cases/{quote(case_id, safe='')}/summary",
            method="GET",
            timeout_seconds=timeout_seconds,
        )

    def export_evidence_bundle(
        self,
        case_id: str,
        output_path: str | Path,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        if not self.config.enabled:
            raise GuestAgentError("Guest Agent is disabled in config", source="local")
        url = urljoin(
            self.base_url,
            f"cases/{quote(case_id, safe='')}/evidence-bundle",
        )
        try:
            response = self.network.request_binary(
                method="GET",
                url=url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout_seconds=(
                    self.config.timeout_seconds
                    if timeout_seconds is None
                    else timeout_seconds
                ),
            )
        except Exception as exc:
            raise GuestAgentError(
                _format_network_error("evidence-bundle", exc),
                source="network",
            ) from exc

        if not 200 <= response.status < 300:
            return _decode_guest_agent_response("evidence-bundle", response)

        destination = Path(output_path)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(response.body)
        except OSError as exc:
            raise GuestAgentError(
                "failed to write evidence bundle output",
                source="local",
            ) from exc
        manifest = _read_bundle_manifest(response.body)
        return GuestAgentResponse(
            status="ok",
            message="evidence bundle saved",
            data={
                "case_id": case_id,
                "output_path": str(destination),
                "size": len(response.body),
                "sha256": hashlib.sha256(response.body).hexdigest(),
                "content_type": str(response.headers.get("content-type", "")),
                "manifest": manifest,
                "trust_model": str(manifest.get("trust_model", "")),
                "forensic_grade": bool(manifest.get("forensic_grade", False)),
                "raw_binary_included": bool(manifest.get("raw_binary_included", False)),
            },
        )

    def collect_logs(
        self,
        case_id: str,
        product_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        return self._request(
            f"cases/{quote(case_id, safe='')}/collection/{quote(product_id, safe='')}",
            method="POST",
            timeout_seconds=timeout_seconds,
        )

    def collection_status(self, case_id: str) -> GuestAgentResponse:
        return self._request(
            f"cases/{quote(case_id, safe='')}/collection/status",
            method="GET",
        )

    def check_security_product_readiness(
        self,
        case_id: str,
        product_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        return self._request(
            "cases/"
            f"{quote(case_id, safe='')}/security-product-readiness/"
            f"{quote(product_id, safe='')}",
            method="POST",
            timeout_seconds=timeout_seconds,
        )

    def security_product_readiness_status(
        self,
        case_id: str,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        return self._request(
            f"cases/{quote(case_id, safe='')}/security-product-readiness/status",
            method="GET",
            timeout_seconds=timeout_seconds,
        )

    def execution_status(
        self,
        case_id: str,
        mark_timeout: bool = False,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        path = f"cases/{quote(case_id, safe='')}/execution-status"
        query: dict[str, str] = {}
        if mark_timeout:
            query["mark_timeout"] = "true"
        if timeout_seconds is not None:
            query["timeout_seconds"] = f"{timeout_seconds:g}"
        if query:
            path += "?" + urlencode(query)
        return self._request(path, method="GET")

    def case_action(
        self,
        case_id: str,
        action: str,
        payload: Mapping[str, Any] | None = None,
    ) -> GuestAgentResponse:
        body = {**dict(payload or {}), "action": action}
        headers: dict[str, str] = {"Authorization": f"Bearer {self.token}"}
        if self.config.execution.enabled:
            execution_token = self.env.get(self.config.execution.token_env, "").strip()
            if not execution_token:
                raise GuestAgentError(
                    "Guest Agent execution token environment variable "
                    f"{self.config.execution.token_env!r} is not set",
                    source="local",
                )
            headers["X-Execution-Token"] = execution_token
        return self._request(
            f"cases/{quote(case_id, safe='')}/actions",
            method="POST",
            payload=body,
            headers=headers,
            timeout_seconds=self.config.execution.timeout_seconds,
        )

    def execute_uploaded_sample(
        self,
        case_id: str,
        sample_id: str,
        expected_sha256: str = "",
        dry_run: bool = True,
        run_id: str = "",
    ) -> GuestAgentResponse:
        action = (
            "dry_run_execute_uploaded_sample" if dry_run else "execute_uploaded_sample"
        )
        payload = {
            "sample_id": sample_id,
            "expected_sha256": expected_sha256,
        }
        if run_id:
            payload["run_id"] = run_id
        return self.case_action(
            case_id=case_id,
            action=action,
            payload=payload,
        )

    def upload_sample(
        self,
        case_id: str,
        sample_id: str,
        file_path: str | Path,
        sha256: str = "",
        upload_token_env: str = UPLOAD_TOKEN_ENV,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        if not self.config.enabled:
            raise GuestAgentError("Guest Agent is disabled in config", source="local")
        upload_token = self.env.get(upload_token_env, "").strip()
        if not upload_token:
            raise GuestAgentError(
                "Guest Agent upload token environment variable "
                f"{upload_token_env!r} is not set",
                source="local",
            )

        path = Path(file_path)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise GuestAgentError(
                "Guest Agent upload file could not be read",
                source="local",
            ) from exc

        url = urljoin(
            self.base_url,
            f"cases/{quote(case_id, safe='')}/sample",
        )
        try:
            response = self.network.request_bytes(
                method="POST",
                url=url,
                body=content,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-Upload-Token": upload_token,
                    "X-Sample-Id": sample_id,
                    "X-Sample-Sha256": sha256,
                    "X-Original-Filename": path.name,
                },
                timeout_seconds=(
                    self.config.timeout_seconds
                    if timeout_seconds is None
                    else timeout_seconds
                ),
            )
        except HTTPError as exc:
            raise _guest_agent_error_from_http_error("upload-sample", exc) from exc
        except Exception as exc:
            raise GuestAgentError(
                _format_network_error("upload-sample", exc),
                source="network",
            ) from exc

        return _decode_guest_agent_response("upload-sample", response)

    def _load_token(self) -> str:
        if not self.config.enabled:
            return ""
        token = self.env.get(self.config.token_env, "").strip()
        if not token:
            raise GuestAgentError(
                "Guest Agent is enabled but token environment variable "
                f"{self.config.token_env!r} is not set",
                source="local",
            )
        return token

    def _request(
        self,
        path: str,
        method: str,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        if not self.config.enabled:
            raise GuestAgentError("Guest Agent is disabled in config", source="local")

        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.network.request_json(
                method=method,
                url=url,
                payload=payload,
                headers=dict(headers or {"Authorization": f"Bearer {self.token}"}),
                timeout_seconds=(
                    self.config.timeout_seconds
                    if timeout_seconds is None
                    else timeout_seconds
                ),
            )
        except HTTPError as exc:
            raise _guest_agent_error_from_http_error(path, exc) from exc
        except Exception as exc:
            raise GuestAgentError(
                _format_network_error(path, exc),
                source="network",
            ) from exc

        return _decode_guest_agent_response(path, response)


def _prepare_case_payload(case: TestCase) -> dict[str, Any]:
    return {
        "case": {
            "id": case.id,
        },
        "sample": {
            "id": case.sample.id,
            "sha256": case.sample.sha256,
            "category": case.sample.category,
            "cloud_object_uri": case.sample.cloud_object_uri,
            "expected_behaviors": list(case.sample.expected_behaviors),
            "notes": case.sample.notes,
        },
        "vm": {
            "id": case.vm.id,
            "provider": case.vm.provider,
            "region": case.vm.region,
            "instance_id": case.vm.instance_id or case.vm.cloud_instance_id,
            "baseline_snapshot": case.vm.baseline_snapshot,
            "network_profile": case.vm.network_profile,
            "product_id": case.vm.product_id,
        },
        "product": {
            "id": case.product.id,
            "display_name": case.product.display_name,
            "vendor": case.product.vendor,
        },
    }


def _decode_guest_agent_response(
    path: str,
    response: NetworkResponse,
) -> GuestAgentResponse:
    try:
        decoded = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        if not 200 <= response.status < 300:
            raise GuestAgentError(
                _format_http_error(path, response.status, response.reason),
                status_code=response.status,
                source="remote",
            ) from exc
        raise GuestAgentError(
            f"Guest Agent {path} response is not valid JSON",
            source="remote",
        ) from exc

    if not isinstance(decoded, dict):
        if not 200 <= response.status < 300:
            raise GuestAgentError(
                _format_http_error(path, response.status, response.reason),
                status_code=response.status,
                source="remote",
            )
        raise GuestAgentError(
            f"Guest Agent {path} response JSON must be an object",
            source="remote",
        )

    if not 200 <= response.status < 300:
        raise GuestAgentError(
            _format_http_error(
                path,
                response.status,
                response.reason,
                _extract_error_message(decoded),
            ),
            status_code=response.status,
            source="remote",
        )

    data_value = decoded.get("data", {})
    data = data_value if isinstance(data_value, dict) else {"value": data_value}
    return GuestAgentResponse(
        status=str(decoded.get("status", "ok")),
        message=str(decoded.get("message", "")),
        data=dict(data),
    )


def _guest_agent_error_from_http_error(path: str, exc: HTTPError) -> GuestAgentError:
    response = NetworkResponse(
        status=exc.code,
        headers=dict(exc.headers.items()) if exc.headers is not None else {},
        body=exc.read(),
        reason=str(exc.reason or ""),
    )
    try:
        _decode_guest_agent_response(path, response)
    except GuestAgentError as error:
        return error
    return GuestAgentError(
        _format_http_error(path, exc.code, str(exc.reason or "")),
        status_code=exc.code,
        source="remote",
    )


def _format_network_error(path: str, exc: BaseException) -> str:
    return (
        "无法连接到 Guest Agent，请确认云端服务已启动、IP/端口/防火墙/"
        f"代理配置正确。request={path}, cause={type(exc).__name__}"
    )


def _format_http_error(
    path: str,
    status_code: int,
    reason: str = "",
    message: str = "",
) -> str:
    status_text = f"HTTP {status_code}"
    if reason:
        status_text += f" {reason}"
    details = f": {message}" if message else ""
    return f"Guest Agent {path} returned {status_text}{details}"


def _extract_error_message(decoded: Mapping[str, Any]) -> str:
    for key in ("message", "detail", "error"):
        value = decoded.get(key)
        if isinstance(value, str) and value:
            return value
    detail = decoded.get("detail")
    if isinstance(detail, list):
        return json.dumps(detail, ensure_ascii=False)
    return ""


def _read_bundle_manifest(content: bytes) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            decoded = json.loads(bundle.read("manifest.json").decode("utf-8"))
    except (
        KeyError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ):
        return {}
    return decoded if isinstance(decoded, dict) else {}
