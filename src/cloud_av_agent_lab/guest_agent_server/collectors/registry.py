from __future__ import annotations

from collections.abc import Callable

from .base import ProductLogCollector
from .huorong import HuorongLogCollector
from .windows_defender import WindowsDefenderLogCollector

CollectorFactory = Callable[[], ProductLogCollector]

SUPPORTED_COLLECTORS: dict[str, CollectorFactory] = {
    "huorong": HuorongLogCollector,
    "windows-defender": WindowsDefenderLogCollector,
}


def get_product_log_collector(product_id: str) -> ProductLogCollector | None:
    normalized = str(product_id or "").strip().casefold()
    factory = SUPPORTED_COLLECTORS.get(normalized)
    return factory() if factory is not None else None


def supported_product_log_collectors() -> tuple[str, ...]:
    return tuple(sorted(SUPPORTED_COLLECTORS))
