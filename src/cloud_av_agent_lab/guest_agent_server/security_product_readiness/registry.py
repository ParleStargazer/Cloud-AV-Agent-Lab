from __future__ import annotations

from cloud_av_agent_lab.guest_agent_server.workspace.io import _utc_now

from .base import (
    SecurityProductReadinessCheck,
    SecurityProductReadinessContext,
    SecurityProductReadinessResult,
)
from .huorong import HuorongSecurityProductReadinessProbe
from .qihoo_360 import Qihoo360SecurityProductReadinessProbe
from .windows_defender import WindowsDefenderSecurityProductReadinessProbe

SUPPORTED_SECURITY_PRODUCT_READINESS_PROBES = {
    "huorong": HuorongSecurityProductReadinessProbe,
    "qihoo-360": Qihoo360SecurityProductReadinessProbe,
    "windows-defender": WindowsDefenderSecurityProductReadinessProbe,
}


def run_security_product_readiness_probe(
    context: SecurityProductReadinessContext,
    product_id: str,
) -> SecurityProductReadinessResult:
    normalized_product = str(product_id or "").strip().casefold()
    probe_type = SUPPORTED_SECURITY_PRODUCT_READINESS_PROBES.get(normalized_product)
    if probe_type is None:
        return SecurityProductReadinessResult(
            product_id=str(product_id or ""),
            state="unsupported",
            confidence="low",
            scope="log_observability",
            protection_state="unknown",
            checked_at_utc=_utc_now(),
            checks=(
                SecurityProductReadinessCheck(
                    name="security_product_readiness_probe_supported",
                    status="unknown",
                    message=(
                        "security product readiness probe is not supported for "
                        f"{product_id!r}"
                    ),
                    data={"supported_products": sorted(_supported_products())},
                ),
            ),
            warnings=(
                "Unsupported security product readiness does not prove the product "
                "is ready or that no detection occurred.",
            ),
            errors=(),
        )
    return probe_type().check(context)


def supported_security_product_readiness_probes() -> tuple[str, ...]:
    return tuple(SUPPORTED_SECURITY_PRODUCT_READINESS_PROBES)


def _supported_products() -> tuple[str, ...]:
    return supported_security_product_readiness_probes()
