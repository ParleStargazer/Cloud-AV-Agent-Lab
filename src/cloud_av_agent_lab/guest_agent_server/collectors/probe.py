from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .base import CollectionWindow


@dataclass(frozen=True)
class ProductObservationProbeResult:
    product_id: str
    probe_state: str
    observed: bool
    attribution_level: str = "none"
    confidence: str = "low"
    reason_codes: Sequence[str] = field(default_factory=tuple)
    evidence_count: int = 0
    observed_at_utc: str = ""
    safe_summary: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "probe_state": self.probe_state,
            "observed": self.observed,
            "attribution_level": self.attribution_level,
            "confidence": self.confidence,
            "reason_codes": list(self.reason_codes),
            "evidence_count": self.evidence_count,
            "observed_at_utc": self.observed_at_utc,
            "safe_summary": dict(self.safe_summary),
        }


class ProductObservationProbe(Protocol):
    product_id: str

    def probe(
        self,
        workspace: Path,
        case_context: Mapping[str, Any],
        window: CollectionWindow,
    ) -> ProductObservationProbeResult:
        """Return a lightweight product-side observation for orchestration timing."""
