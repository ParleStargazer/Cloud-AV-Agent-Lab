from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CaseStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    DETECTED = "detected"
    MISSED = "missed"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass(frozen=True)
class LabPolicy:
    name: str
    artifact_dir: str = "reports"
    local_sample_storage: str = "forbidden"
    require_cloud_isolation: bool = True
    max_case_seconds: int = 900


@dataclass(frozen=True)
class CloudProfile:
    provider: str
    region: str
    credential_profile_env: str
    artifact_bucket: str
    network_profile: str
    api_endpoint: str = "https://lighthouse.tencentcloudapi.com"
    api_version: str = "2020-03-24"
    secret_id: str = ""
    secret_key: str = ""
    mode: str = "mock"
    dry_run: bool = True


@dataclass(frozen=True)
class ProxyConfig:
    enabled: bool = False
    type: str = "http"
    host: str = ""
    port: int = 0


@dataclass(frozen=True)
class NetworkConfig:
    proxy: ProxyConfig = field(default_factory=ProxyConfig)


@dataclass(frozen=True)
class GuestAgentExecutionConfig:
    enabled: bool = False
    token_env: str = "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class GuestAgentDesktopWorkerConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8001"
    token_env: str = "CLOUD_AV_DESKTOP_WORKER_TOKEN"
    timeout_seconds: float = 5.0
    required_for_execution: bool = True
    expected_user: str = "Administrator"
    require_interactive_session: bool = True


@dataclass(frozen=True)
class GuestAgentConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8080"
    token_env: str = "CLOUD_AV_GUEST_AGENT_TOKEN"
    timeout_seconds: float = 10.0
    execution: GuestAgentExecutionConfig = field(
        default_factory=GuestAgentExecutionConfig
    )
    desktop_worker: GuestAgentDesktopWorkerConfig = field(
        default_factory=GuestAgentDesktopWorkerConfig
    )


@dataclass(frozen=True)
class ProductProfile:
    id: str
    display_name: str
    vendor: str
    enabled: bool = True
    log_paths: tuple[str, ...] = ()
    ui_window_titles: tuple[str, ...] = ()
    detection_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class VmProfile:
    id: str
    provider: str
    region: str
    image: str
    baseline_snapshot: str
    product_id: str
    network_profile: str
    cloud_instance_id: str = ""
    instance_id: str = ""


@dataclass(frozen=True)
class SampleReference:
    id: str
    sha256: str
    category: str
    cloud_object_uri: str
    expected_behaviors: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class TestCase:
    id: str
    sample: SampleReference
    vm: VmProfile
    product: ProductProfile


@dataclass(frozen=True)
class AvSignal:
    product_id: str
    signal_type: str
    verdict: str
    title: str
    detail: str
    confidence: float
    source: str = ""


@dataclass
class CaseResult:
    case: TestCase
    status: CaseStatus = CaseStatus.PLANNED
    signals: list[AvSignal] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def detected(self) -> bool:
        return any(signal.verdict == "detected" for signal in self.signals)


@dataclass(frozen=True)
class LabConfig:
    policy: LabPolicy
    cloud: CloudProfile
    network: NetworkConfig
    guest_agent: GuestAgentConfig
    products: dict[str, ProductProfile]
    vms: dict[str, VmProfile]
    samples: dict[str, SampleReference]
