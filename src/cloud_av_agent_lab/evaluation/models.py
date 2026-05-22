from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvaluationSummary:
    case_id: str
    sample_id: str
    vm_id: str
    product_id: str
    verdict: str
    confidence: str
    summary: str
    reasons: Sequence[str] = field(default_factory=tuple)
    delivery: Mapping[str, Any] = field(default_factory=dict)
    execution: Mapping[str, Any] = field(default_factory=dict)
    collection: Mapping[str, Any] = field(default_factory=dict)
    environment: Mapping[str, Any] = field(default_factory=dict)
    decision_inputs: Mapping[str, Any] = field(default_factory=dict)
    blocking_conditions: Sequence[str] = field(default_factory=tuple)
    nonfatal_failures: Sequence[str] = field(default_factory=tuple)
    timeline: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    generated_at_utc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "sample_id": self.sample_id,
            "vm_id": self.vm_id,
            "product_id": self.product_id,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "delivery": dict(self.delivery),
            "execution": dict(self.execution),
            "collection": dict(self.collection),
            "environment": dict(self.environment),
            "decision_inputs": dict(self.decision_inputs),
            "blocking_conditions": list(self.blocking_conditions),
            "nonfatal_failures": list(self.nonfatal_failures),
            "timeline": [dict(item) for item in self.timeline],
            "generated_at_utc": self.generated_at_utc,
        }
