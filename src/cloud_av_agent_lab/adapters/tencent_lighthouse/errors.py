from __future__ import annotations

from cloud_av_agent_lab.adapters.cloud import CloudProviderError


class TencentCloudConfigError(CloudProviderError):
    """Raised when Tencent Cloud adapter configuration is invalid."""


class TencentCloudApiError(CloudProviderError):
    """Raised when Tencent Cloud returns an API error response."""

    def __init__(self, code: str, message: str, request_id: str = "") -> None:
        self.code = code
        self.request_id = request_id
        detail = f"{code}: {message}"
        if request_id:
            detail = f"{detail} (RequestId: {request_id})"
        super().__init__(detail)
