from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CollectionWindow:
    start_utc: str
    end_utc: str
    case_prepared_at_utc: str = ""
    uploaded_at_utc: str = ""
    execution_started_at_utc: str = ""
    collection_started_at_utc: str = ""
    collection_finished_at_utc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "case_prepared_at_utc": self.case_prepared_at_utc,
            "uploaded_at_utc": self.uploaded_at_utc,
            "execution_started_at_utc": self.execution_started_at_utc,
            "collection_started_at_utc": self.collection_started_at_utc,
            "collection_finished_at_utc": self.collection_finished_at_utc,
        }


@dataclass(frozen=True)
class NormalizedSecurityEvent:
    timestamp_utc: str
    source: str
    event_type: str
    case_id: str
    sample_id: str
    product_id: str
    confidence: str
    message: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    raw_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "source": self.source,
            "event_type": self.event_type,
            "case_id": self.case_id,
            "sample_id": self.sample_id,
            "product_id": self.product_id,
            "confidence": self.confidence,
            "message": self.message,
            "evidence": dict(self.evidence),
            "raw_ref": self.raw_ref,
        }


@dataclass(frozen=True)
class CollectorResult:
    product_id: str
    collection_state: str
    verdict: str
    intercepted: bool | None
    reason: str
    evidence_count: int
    events: Sequence[NormalizedSecurityEvent] = field(default_factory=tuple)
    errors: Sequence[str] = field(default_factory=tuple)
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    window: CollectionWindow | None = None
    collected_at_utc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "collection_state": self.collection_state,
            "verdict": self.verdict,
            "intercepted": self.intercepted,
            "reason": self.reason,
            "evidence_count": self.evidence_count,
            "events": [event.to_dict() for event in self.events],
            "errors": list(self.errors),
            "artifacts": dict(self.artifacts),
            "window": self.window.to_dict() if self.window is not None else {},
            "collected_at_utc": self.collected_at_utc,
        }


class ProductLogCollector(ABC):
    product_id: str

    @abstractmethod
    def collect(
        self,
        workspace: Path,
        case_context: Mapping[str, Any],
        window: CollectionWindow,
    ) -> CollectorResult:
        """Collect and normalize product logs for one prepared case."""
