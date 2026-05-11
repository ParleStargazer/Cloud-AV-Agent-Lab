from __future__ import annotations

from cloud_av_agent_lab.adapters.cloud import CloudVmAdapter
from cloud_av_agent_lab.adapters.null_cloud import PlannedCloudVmAdapter
from cloud_av_agent_lab.adapters.tencent_cloud import TencentCloudLighthouseAdapter
from cloud_av_agent_lab.core.contracts import LabConfig
from cloud_av_agent_lab.network.client import NetworkClient


def create_cloud_adapter(
    config: LabConfig,
    network: NetworkClient | None = None,
    dry_run: bool | None = None,
    confirmed_instance_id: str = "",
    poll_timeout_seconds: float = 600.0,
    poll_interval_seconds: float = 5.0,
) -> CloudVmAdapter:
    provider = config.cloud.provider.casefold()
    network_client = network or NetworkClient.from_config(config.network)
    if provider in {"tencent-cloud-lighthouse", "lighthouse", "tencent-cloud"}:
        return TencentCloudLighthouseAdapter(
            config.cloud,
            network=network_client,
            dry_run=dry_run,
            confirmed_instance_id=confirmed_instance_id,
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    return PlannedCloudVmAdapter()
