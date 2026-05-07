from __future__ import annotations

from cloud_av_agent_lab.adapters.cloud import CloudVmAdapter
from cloud_av_agent_lab.adapters.null_cloud import PlannedCloudVmAdapter
from cloud_av_agent_lab.adapters.tencent_cloud import TencentCloudLighthouseAdapter
from cloud_av_agent_lab.core.contracts import LabConfig
from cloud_av_agent_lab.network.client import NetworkClient


def create_cloud_adapter(
    config: LabConfig,
    network: NetworkClient | None = None,
) -> CloudVmAdapter:
    provider = config.cloud.provider.casefold()
    network_client = network or NetworkClient.from_config(config.network)
    if provider in {"tencent-cloud-lighthouse", "lighthouse", "tencent-cloud"}:
        return TencentCloudLighthouseAdapter(config.cloud, network=network_client)
    return PlannedCloudVmAdapter()
