from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

from .core.contracts import (
    CloudProfile,
    LabConfig,
    LabPolicy,
    NetworkConfig,
    ProductProfile,
    ProxyConfig,
    SampleReference,
    VmProfile,
)


class ConfigError(ValueError):
    """Raised when a lab config is malformed."""


def _require_table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"missing [{name}] table")
    return value


def _require_list(data: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = data.get(name)
    if not isinstance(value, list):
        raise ConfigError(f"missing [[{name}]] entries")
    return value


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError("expected a list of strings")
    return tuple(value)


def _optional_table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _load_network_config(data: dict[str, Any]) -> NetworkConfig:
    network = _optional_table(data, "network")
    proxy = network.get("proxy", {})
    if not isinstance(proxy, dict):
        raise ConfigError("[network.proxy] must be a table")

    enabled = bool(proxy.get("enabled", False))
    return NetworkConfig(
        proxy=ProxyConfig(
            enabled=enabled,
            type=str(proxy.get("type", "http")),
            host=str(proxy.get("host", "")),
            port=int(proxy.get("port", 0)),
        )
    )


def load_config(path: str | Path) -> LabConfig:
    config_path = Path(path)
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))

    lab = _require_table(data, "lab")
    cloud = _require_table(data, "cloud")

    policy = LabPolicy(
        name=str(lab["name"]),
        artifact_dir=str(lab.get("artifact_dir", "reports")),
        local_sample_storage=str(lab.get("local_sample_storage", "forbidden")),
        require_cloud_isolation=bool(lab.get("require_cloud_isolation", True)),
        max_case_seconds=int(lab.get("max_case_seconds", 900)),
    )

    cloud_profile = CloudProfile(
        provider=str(cloud["provider"]),
        region=str(cloud["region"]),
        credential_profile_env=str(cloud["credential_profile_env"]),
        artifact_bucket=str(cloud["artifact_bucket"]),
        network_profile=str(cloud["network_profile"]),
        api_endpoint=str(
            cloud.get("api_endpoint", "https://lighthouse.tencentcloudapi.com")
        ),
        api_version=str(cloud.get("api_version", "2020-03-24")),
        secret_id=str(cloud.get("secret_id", "")),
        secret_key=str(cloud.get("secret_key", "")),
        mode=str(cloud.get("mode", "mock")),
        dry_run=bool(cloud.get("dry_run", True)),
    )
    network_config = _load_network_config(data)

    products = {
        str(item["id"]): ProductProfile(
            id=str(item["id"]),
            display_name=str(item["display_name"]),
            vendor=str(item["vendor"]),
            log_paths=_tuple(item.get("log_paths")),
            ui_window_titles=_tuple(item.get("ui_window_titles")),
            detection_keywords=_tuple(item.get("detection_keywords")),
        )
        for item in _require_list(data, "products")
    }

    vms = {
        str(item["id"]): VmProfile(
            id=str(item["id"]),
            provider=str(item["provider"]),
            region=str(item["region"]),
            image=str(item["image"]),
            baseline_snapshot=str(item["baseline_snapshot"]),
            product_id=str(item["product_id"]),
            network_profile=str(item["network_profile"]),
            cloud_instance_id=str(item.get("cloud_instance_id", "")),
            instance_id=str(item.get("instance_id", item.get("cloud_instance_id", ""))),
        )
        for item in _require_list(data, "vms")
    }

    samples = {
        str(item["id"]): SampleReference(
            id=str(item["id"]),
            sha256=str(item["sha256"]),
            category=str(item["category"]),
            cloud_object_uri=str(item["cloud_object_uri"]),
            expected_behaviors=_tuple(item.get("expected_behaviors")),
            notes=str(item.get("notes", "")),
        )
        for item in _require_list(data, "samples")
    }

    for vm in vms.values():
        if vm.product_id not in products:
            raise ConfigError(
                f"vm {vm.id!r} references unknown product {vm.product_id!r}"
            )

    return LabConfig(
        policy=policy,
        cloud=cloud_profile,
        network=network_config,
        products=products,
        vms=vms,
        samples=samples,
    )
