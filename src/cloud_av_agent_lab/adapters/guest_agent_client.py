from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urljoin

from cloud_av_agent_lab.network.client import NetworkClient, NetworkResponse


class GuestAgentClient:
    """HTTP client for a future cloud-side Guest Agent.

    This keeps Guest Agent connectivity on the same NetworkClient path as cloud
    APIs, including the optional development proxy.
    """

    def __init__(
        self,
        base_url: str,
        network: NetworkClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.network = network or NetworkClient()

    def request_json(
        self,
        path: str,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> NetworkResponse:
        url = urljoin(self.base_url, path.lstrip("/"))
        return self.network.request_json(
            method=method,
            url=url,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
