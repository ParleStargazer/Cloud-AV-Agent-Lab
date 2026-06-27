from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urljoin

from cloud_av_agent_lab.network.client import NetworkClient, NetworkResponse


class DesktopWorkerClientError(RuntimeError):
    """Raised when Control Agent cannot query the local Desktop Worker."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        source: str = "network",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.source = source


@dataclass(frozen=True)
class DesktopWorkerStatus:
    ready: bool
    data: dict[str, Any]


@dataclass(frozen=True)
class DesktopWorkerResponse:
    status: str
    message: str
    data: dict[str, Any]


class DesktopWorkerClient:
    """Localhost-only client used by Control Agent to query Desktop Worker."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = 5.0,
        network: NetworkClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.network = network or NetworkClient()

    def health(self) -> DesktopWorkerStatus:
        try:
            response = self.network.request_json(
                method="GET",
                url=urljoin(self.base_url, "health"),
                headers={"Authorization": f"Bearer {self.token}"},
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            raise DesktopWorkerClientError(
                f"Desktop Worker health request failed: {type(exc).__name__}",
                source="network",
            ) from exc
        return _decode_worker_status(response)

    def execute(self, payload: Mapping[str, Any]) -> DesktopWorkerResponse:
        try:
            response = self.network.request_json(
                method="POST",
                url=urljoin(self.base_url, "execute"),
                payload=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            raise DesktopWorkerClientError(
                f"Desktop Worker execute request failed: {type(exc).__name__}",
                source="network",
            ) from exc
        return _decode_worker_response("execute", response)

    def execution_status(
        self,
        case_id: str,
        *,
        mark_timeout: bool = False,
        timeout_seconds: float | None = None,
    ) -> DesktopWorkerResponse:
        path = f"execution-status/{quote(case_id, safe='')}"
        query: dict[str, str] = {}
        if mark_timeout:
            query["mark_timeout"] = "true"
        if timeout_seconds is not None:
            query["timeout_seconds"] = f"{timeout_seconds:g}"
        if query:
            path += "?" + urlencode(query)
        try:
            response = self.network.request_json(
                method="GET",
                url=urljoin(self.base_url, path),
                headers={"Authorization": f"Bearer {self.token}"},
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            raise DesktopWorkerClientError(
                f"Desktop Worker execution-status request failed: {type(exc).__name__}",
                source="network",
            ) from exc
        return _decode_worker_response("execution-status", response)

    def product_warmup(self, product_id: str) -> DesktopWorkerResponse:
        path = f"product-actions/warm-up/{quote(product_id, safe='')}"
        try:
            response = self.network.request_json(
                method="POST",
                url=urljoin(self.base_url, path),
                headers={"Authorization": f"Bearer {self.token}"},
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            raise DesktopWorkerClientError(
                f"Desktop Worker product warm-up request failed: {type(exc).__name__}",
                source="network",
            ) from exc
        return _decode_worker_response("product-warmup", response)


def _decode_worker_status(response: NetworkResponse) -> DesktopWorkerStatus:
    decoded = _decode_worker_payload("health", response)
    data_value = decoded.get("data", {})
    data = data_value if isinstance(data_value, dict) else {"value": data_value}
    return DesktopWorkerStatus(ready=True, data=dict(data))


def _decode_worker_response(
    path: str,
    response: NetworkResponse,
) -> DesktopWorkerResponse:
    decoded = _decode_worker_payload(path, response)
    data_value = decoded.get("data", {})
    data = data_value if isinstance(data_value, dict) else {"value": data_value}
    return DesktopWorkerResponse(
        status=str(decoded.get("status", "ok")),
        message=str(decoded.get("message", "")),
        data=dict(data),
    )


def _decode_worker_payload(path: str, response: NetworkResponse) -> dict[str, Any]:
    try:
        decoded = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DesktopWorkerClientError(
            f"Desktop Worker {path} returned invalid JSON: HTTP {response.status}",
            status_code=response.status,
            source="remote",
        ) from exc

    if not isinstance(decoded, dict):
        raise DesktopWorkerClientError(
            f"Desktop Worker {path} response must be an object: HTTP {response.status}",
            status_code=response.status,
            source="remote",
        )
    if not 200 <= response.status < 300:
        message = _error_message(decoded)
        suffix = f": {message}" if message else ""
        raise DesktopWorkerClientError(
            f"Desktop Worker {path} returned HTTP {response.status}{suffix}",
            status_code=response.status,
            source="remote",
        )
    return decoded


def _error_message(decoded: dict[str, Any]) -> str:
    for key in ("message", "detail", "error"):
        value = decoded.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = _nested_error_message(value)
            if nested:
                return nested
    return ""


def _nested_error_message(decoded: Mapping[str, Any]) -> str:
    message = str(decoded.get("message", "")).strip()
    reason_code = str(decoded.get("reason_code", "")).strip()
    error_type = str(decoded.get("error_type", "")).strip()
    parts = [part for part in (message, reason_code, error_type) if part]
    return " ".join(parts)
