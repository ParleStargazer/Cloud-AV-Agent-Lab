from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class SecurityProductReadinessError(RuntimeError):
    """Raised when a security product readiness probe cannot be handled."""


@dataclass(frozen=True)
class SecurityProductReadinessContext:
    product_id: str
    workspace: Path
    log_dir: Path | None = None


@dataclass(frozen=True)
class SecurityProductReadinessCheck:
    name: str
    status: str
    message: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class SecurityProductReadinessResult:
    product_id: str
    state: str
    confidence: str
    scope: str
    protection_state: str
    checked_at_utc: str
    reason_codes: Sequence[str] = field(default_factory=tuple)
    checks: Sequence[SecurityProductReadinessCheck] = field(default_factory=tuple)
    warnings: Sequence[str] = field(default_factory=tuple)
    errors: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "state": self.state,
            "confidence": self.confidence,
            "scope": self.scope,
            "protection_state": self.protection_state,
            "checked_at_utc": self.checked_at_utc,
            "reason_codes": list(self.reason_codes),
            "checks": [check.to_dict() for check in self.checks],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


class SecurityProductReadinessProbe(Protocol):
    product_id: str

    def check(
        self,
        context: SecurityProductReadinessContext,
    ) -> SecurityProductReadinessResult:
        """Run a low-intrusion, read-only readiness check."""
