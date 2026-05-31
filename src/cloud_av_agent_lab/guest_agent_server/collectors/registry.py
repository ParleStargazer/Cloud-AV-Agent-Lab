from __future__ import annotations

from collections.abc import Callable

from .base import ProductLogCollector
from .huorong import HuorongLogCollector
from .qihoo_360 import Qihoo360LogCollector
from .tencent_pc_manager import (
    TencentPcManagerLogCollector,
    TencentPcManagerObservationProbe,
)
from .windows_defender import WindowsDefenderLogCollector
from .probe import ProductObservationProbe

CollectorFactory = Callable[[], ProductLogCollector]
ProbeFactory = Callable[[], ProductObservationProbe]

SUPPORTED_COLLECTORS: dict[str, CollectorFactory] = {
    "huorong": HuorongLogCollector,
    "qihoo-360": Qihoo360LogCollector,
    "tencent-pc-manager": TencentPcManagerLogCollector,
    "windows-defender": WindowsDefenderLogCollector,
}
SUPPORTED_OBSERVATION_PROBES: dict[str, ProbeFactory] = {
    "tencent-pc-manager": TencentPcManagerObservationProbe,
}


def get_product_log_collector(product_id: str) -> ProductLogCollector | None:
    normalized = str(product_id or "").strip().casefold()
    factory = SUPPORTED_COLLECTORS.get(normalized)
    return factory() if factory is not None else None


def supported_product_log_collectors() -> tuple[str, ...]:
    return tuple(sorted(SUPPORTED_COLLECTORS))


def get_product_observation_probe(product_id: str) -> ProductObservationProbe | None:
    normalized = str(product_id or "").strip().casefold()
    factory = SUPPORTED_OBSERVATION_PROBES.get(normalized)
    return factory() if factory is not None else None


def supported_product_observation_probes() -> tuple[str, ...]:
    return tuple(sorted(SUPPORTED_OBSERVATION_PROBES))
