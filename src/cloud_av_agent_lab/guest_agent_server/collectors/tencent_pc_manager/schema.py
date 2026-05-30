from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

PRODUCT_ID = "tencent-pc-manager"
RAW_PRODUCT_NAME = "TAV"
PRODUCT_DISPLAY_NAME = "Tencent PC Manager"

DEFAULT_QQPCMGR_ROOT = r"C:\ProgramData\Tencent\QQPCMgr"
DEFAULT_QUARANTINE_DIR = DEFAULT_QQPCMGR_ROOT + r"\Quarantine"
DEFAULT_TAV_CACHE_PATH = DEFAULT_QQPCMGR_ROOT + r"\TAVWfsDB\TAVCacheFullEx.db"

QUARANTINE_CONTAINER_CATEGORY = "raw_product_quarantine_container"
QUARANTINE_ICON_CATEGORY = "raw_product_quarantine_icon_sidecar"

STRONG_SIZE_DELTA_MIN = 0
STRONG_SIZE_DELTA_MAX = 4096
MEDIUM_ABS_SIZE_DELTA_MAX = 4096


@dataclass(frozen=True)
class TavArtifactMetadata:
    kind: str
    path: str
    name: str
    present: bool
    size: int | None = None
    mtime_utc: str = ""
    ctime_utc: str = ""
    stat_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "name": self.name,
            "present": self.present,
            "size": self.size,
            "mtime_utc": self.mtime_utc,
            "ctime_utc": self.ctime_utc,
            "stat_error": self.stat_error,
        }


@dataclass(frozen=True)
class TavSizeDeltaAssessment:
    original_size: int | None
    observed_size: int | None
    delta: int | None
    level: str
    warnings: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_size": self.original_size,
            "observed_size": self.observed_size,
            "delta": self.delta,
            "level": self.level,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class TavQuarantineObservation:
    sample_md5: str
    quarantine_dir: str
    container: TavArtifactMetadata
    icon_sidecar: TavArtifactMetadata
    size_delta: TavSizeDeltaAssessment
    evidence_level: str
    warnings: Sequence[str] = field(default_factory=tuple)
    schema_version: str = "tav-quarantine-observation.v1"
    product_id: str = PRODUCT_ID
    raw_product: str = RAW_PRODUCT_NAME

    @property
    def container_present(self) -> bool:
        return self.container.present

    @property
    def icon_sidecar_present(self) -> bool:
        return self.icon_sidecar.present

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "product_id": self.product_id,
            "raw_product": self.raw_product,
            "sample_md5": self.sample_md5,
            "quarantine_dir": self.quarantine_dir,
            "container_present": self.container_present,
            "icon_sidecar_present": self.icon_sidecar_present,
            "container": self.container.to_dict(),
            "icon_sidecar": self.icon_sidecar.to_dict(),
            "size_delta": self.size_delta.to_dict(),
            "evidence_level": self.evidence_level,
            "warnings": list(self.warnings),
            "limitations": [
                "TAV log content is encrypted and not parsed",
                "quarantine container content is not read or decrypted",
                "icon sidecar content is not read",
            ],
        }
