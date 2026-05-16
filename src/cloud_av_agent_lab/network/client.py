from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from cloud_av_agent_lab.core.contracts import NetworkConfig, ProxyConfig
from cloud_av_agent_lab.network.proxy import build_proxy_map, build_proxy_url


@dataclass(frozen=True)
class NetworkResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    reason: str = ""


class NetworkClient:
    """Central place for outbound HTTP configuration.

    Business adapters should depend on this client instead of reading proxy
    settings directly. Removing the temporary proxy module later should only
    require changing this package and the optional config shape.
    """

    def __init__(self, proxy: ProxyConfig | None = None) -> None:
        self.proxy = proxy or ProxyConfig()
        self.proxy_url = build_proxy_url(self.proxy)
        self.proxy_map = build_proxy_map(self.proxy)

    @classmethod
    def from_config(cls, config: NetworkConfig) -> "NetworkClient":
        return cls(proxy=config.proxy)

    def build_opener(self) -> urllib.request.OpenerDirector:
        if not self.proxy_map:
            return urllib.request.build_opener()
        return urllib.request.build_opener(urllib.request.ProxyHandler(self.proxy_map))

    def request_json(
        self,
        method: str,
        url: str,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> NetworkResponse:
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **dict(headers or {}),
        }
        body = None
        if payload is not None:
            body = encode_json_payload(payload)

        request = urllib.request.Request(
            url=url,
            data=body,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            response = self.build_opener().open(request, timeout=timeout_seconds)
        except urllib.error.HTTPError as exc:
            return _response_from_http_error(exc)

        with response:
            return NetworkResponse(
                status=response.status,
                headers=dict(response.headers.items()),
                body=response.read(),
                reason=getattr(response, "reason", ""),
            )

    def request_bytes(
        self,
        method: str,
        url: str,
        body: bytes,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> NetworkResponse:
        request_headers = {
            "Content-Type": "application/octet-stream",
            "Accept": "application/json",
            **dict(headers or {}),
        }
        request = urllib.request.Request(
            url=url,
            data=body,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            response = self.build_opener().open(request, timeout=timeout_seconds)
        except urllib.error.HTTPError as exc:
            return _response_from_http_error(exc)

        with response:
            return NetworkResponse(
                status=response.status,
                headers=dict(response.headers.items()),
                body=response.read(),
                reason=getattr(response, "reason", ""),
            )

    def request_binary(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> NetworkResponse:
        request_headers = {
            "Accept": "application/zip,application/octet-stream,*/*",
            **dict(headers or {}),
        }
        request = urllib.request.Request(
            url=url,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            response = self.build_opener().open(request, timeout=timeout_seconds)
        except urllib.error.HTTPError as exc:
            return _response_from_http_error(exc)

        with response:
            return NetworkResponse(
                status=response.status,
                headers=dict(response.headers.items()),
                body=response.read(),
                reason=getattr(response, "reason", ""),
            )


def encode_json_payload(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _response_from_http_error(exc: urllib.error.HTTPError) -> NetworkResponse:
    return NetworkResponse(
        status=exc.code,
        headers=dict(exc.headers.items()) if exc.headers is not None else {},
        body=exc.read(),
        reason=str(exc.reason or ""),
    )
