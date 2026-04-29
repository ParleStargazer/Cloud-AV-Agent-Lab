from __future__ import annotations

from cloud_av_agent_lab.core.contracts import ProxyConfig


SUPPORTED_PROXY_TYPES = {"http", "https", "socks5"}


class ProxyConfigError(ValueError):
    """Raised when proxy configuration is malformed."""


def build_proxy_url(config: ProxyConfig) -> str | None:
    if not config.enabled:
        return None

    proxy_type = config.type.casefold()
    if proxy_type not in SUPPORTED_PROXY_TYPES:
        supported = ", ".join(sorted(SUPPORTED_PROXY_TYPES))
        raise ProxyConfigError(f"proxy type must be one of: {supported}")
    if not config.host:
        raise ProxyConfigError("proxy host is required when proxy is enabled")
    if config.port <= 0:
        raise ProxyConfigError("proxy port must be positive when proxy is enabled")

    return f"{proxy_type}://{config.host}:{config.port}"


def build_proxy_map(config: ProxyConfig) -> dict[str, str]:
    proxy_url = build_proxy_url(config)
    if proxy_url is None:
        return {}
    return {"http": proxy_url, "https": proxy_url}
