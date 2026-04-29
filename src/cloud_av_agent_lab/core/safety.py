from __future__ import annotations

import re

from cloud_av_agent_lab.network.proxy import ProxyConfigError, build_proxy_url

from .contracts import LabConfig, SampleReference


class SafetyError(ValueError):
    """Raised when configuration violates the local safety boundary."""


_LOCAL_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")


def is_local_sample_reference(uri: str) -> bool:
    value = uri.strip()
    lower = value.lower()
    if not value:
        return True
    if lower.startswith(("file:", "file://")):
        return True
    if _LOCAL_DRIVE_RE.match(value):
        return True
    if value.startswith(("/", "\\", "./", "../", "~")):
        return True
    return False


def validate_sample_reference(sample: SampleReference) -> list[str]:
    errors: list[str] = []
    if is_local_sample_reference(sample.cloud_object_uri):
        errors.append(
            f"sample {sample.id!r} uses a local path; use a cloud object URI instead"
        )
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sample.sha256):
        errors.append(f"sample {sample.id!r} sha256 must be 64 hex characters")
    return errors


def assert_safe_config(config: LabConfig) -> None:
    errors: list[str] = []

    if config.policy.local_sample_storage != "forbidden":
        errors.append("lab.local_sample_storage must be 'forbidden'")
    if not config.policy.require_cloud_isolation:
        errors.append("lab.require_cloud_isolation must be true")
    if config.cloud.mode.casefold() not in {"mock", "real"}:
        errors.append("cloud.mode must be 'mock' or 'real'")

    for sample in config.samples.values():
        errors.extend(validate_sample_reference(sample))

    for vm in config.vms.values():
        if "isolated" not in vm.network_profile.lower():
            errors.append(f"vm {vm.id!r} should use an isolated network profile")

    try:
        build_proxy_url(config.network.proxy)
    except ProxyConfigError as exc:
        errors.append(str(exc))

    if errors:
        raise SafetyError("; ".join(errors))
