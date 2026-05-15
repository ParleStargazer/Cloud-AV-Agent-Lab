from __future__ import annotations

import os
import re
from collections.abc import Mapping

from cloud_av_agent_lab.core.contracts import CloudProfile

from .models import TencentCloudAuth


def resolve_tencent_cloud_auth(
    cloud: CloudProfile,
    env: Mapping[str, str] | None = None,
) -> TencentCloudAuth:
    values = env if env is not None else os.environ
    secret_id, secret_id_source = _env_or_config(
        values,
        "TENCENTCLOUD_SECRET_ID",
        cloud.secret_id,
    )
    secret_key, secret_key_source = _env_or_config(
        values,
        "TENCENTCLOUD_SECRET_KEY",
        cloud.secret_key,
    )
    region, region_source = _env_or_config(
        values,
        "TENCENTCLOUD_REGION",
        cloud.region,
    )
    return TencentCloudAuth(
        secret_id=secret_id,
        secret_key=secret_key,
        region=region,
        secret_id_source=secret_id_source,
        secret_key_source=secret_key_source,
        region_source=region_source,
    )


def _env_or_config(
    env: Mapping[str, str],
    key: str,
    config_value: str,
) -> tuple[str, str]:
    env_value = env.get(key, "")
    if env_value:
        return env_value, "env"
    return config_value, "config"


def _env_suffix(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
