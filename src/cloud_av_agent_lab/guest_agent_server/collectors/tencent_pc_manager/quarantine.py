from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .schema import (
    MEDIUM_ABS_SIZE_DELTA_MAX,
    STRONG_SIZE_DELTA_MAX,
    STRONG_SIZE_DELTA_MIN,
    TavArtifactMetadata,
    TavQuarantineObservation,
    TavSizeDeltaAssessment,
)

MD5_RE = re.compile(r"^[0-9a-f]{32}$")


def normalize_tav_md5(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not MD5_RE.fullmatch(normalized):
        raise ValueError("TAV sample md5 must be 32 lowercase hex characters")
    return normalized


def observe_tav_quarantine(
    quarantine_dir: str | Path,
    sample_md5: str,
    original_sample_size: int | None = None,
) -> TavQuarantineObservation:
    md5 = normalize_tav_md5(sample_md5)
    root = Path(quarantine_dir)
    container = stat_tav_artifact(root / md5, kind="quarantine_container")
    icon_sidecar = stat_tav_artifact(root / f"{md5}.ico", kind="icon_sidecar")
    size_delta = assess_container_size_delta(
        observed_size=container.size,
        original_size=original_sample_size,
    )
    warnings = list(size_delta.warnings)

    if container.present:
        evidence_level = _container_evidence_level(size_delta.level)
    elif icon_sidecar.present:
        evidence_level = "weak"
        warnings.append("quarantine_icon_sidecar_without_container")
    else:
        evidence_level = "unknown"

    return TavQuarantineObservation(
        sample_md5=md5,
        quarantine_dir=str(root),
        container=container,
        icon_sidecar=icon_sidecar,
        size_delta=size_delta,
        evidence_level=evidence_level,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def stat_tav_artifact(path: str | Path, kind: str) -> TavArtifactMetadata:
    artifact_path = Path(path)
    try:
        stat_result = artifact_path.stat()
    except FileNotFoundError:
        return TavArtifactMetadata(
            kind=kind,
            path=str(artifact_path),
            name=artifact_path.name,
            present=False,
        )
    except OSError as exc:
        return TavArtifactMetadata(
            kind=kind,
            path=str(artifact_path),
            name=artifact_path.name,
            present=artifact_path.exists(),
            stat_error=type(exc).__name__,
        )

    return TavArtifactMetadata(
        kind=kind,
        path=str(artifact_path),
        name=artifact_path.name,
        present=True,
        size=stat_result.st_size,
        mtime_utc=_timestamp_to_utc(stat_result.st_mtime),
        ctime_utc=_timestamp_to_utc(stat_result.st_ctime),
    )


def assess_container_size_delta(
    observed_size: int | None,
    original_size: int | None,
) -> TavSizeDeltaAssessment:
    if observed_size is None:
        return TavSizeDeltaAssessment(
            original_size=original_size,
            observed_size=observed_size,
            delta=None,
            level="unknown",
            warnings=("quarantine_container_size_unavailable",),
        )
    if original_size is None or original_size < 0:
        return TavSizeDeltaAssessment(
            original_size=original_size,
            observed_size=observed_size,
            delta=None,
            level="unknown",
            warnings=("original_sample_size_missing",),
        )

    delta = observed_size - original_size
    if STRONG_SIZE_DELTA_MIN <= delta <= STRONG_SIZE_DELTA_MAX:
        return TavSizeDeltaAssessment(
            original_size=original_size,
            observed_size=observed_size,
            delta=delta,
            level="strong",
        )
    if delta < 0 and abs(delta) <= MEDIUM_ABS_SIZE_DELTA_MAX:
        return TavSizeDeltaAssessment(
            original_size=original_size,
            observed_size=observed_size,
            delta=delta,
            level="medium",
            warnings=("quarantine_container_size_delta_negative",),
        )
    return TavSizeDeltaAssessment(
        original_size=original_size,
        observed_size=observed_size,
        delta=delta,
        level="weak",
        warnings=("quarantine_container_size_delta_out_of_range",),
    )


def _container_evidence_level(size_delta_level: str) -> str:
    if size_delta_level in {"strong", "medium"}:
        return size_delta_level
    return "medium"


def _timestamp_to_utc(value: float) -> str:
    return (
        datetime.fromtimestamp(value, timezone.utc)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )
