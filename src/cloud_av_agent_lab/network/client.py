from __future__ import annotations

import json
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
            body = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url=url,
            data=body,
            headers=request_headers,
            method=method.upper(),
        )
        with self.build_opener().open(request, timeout=timeout_seconds) as response:
            return NetworkResponse(
                status=response.status,
                headers=dict(response.headers.items()),
                body=response.read(),
            )
