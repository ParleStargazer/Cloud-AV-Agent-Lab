from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cloud_av_agent_lab.guest_agent_server.collectors.base import (
    CollectionWindow,
    CollectorArtifact,
    CollectorResult,
    NormalizedSecurityEvent,
    ProductLogCollector,
)
from cloud_av_agent_lab.guest_agent_server.workspace.io import _utc_now

from .quarantine import normalize_tav_md5, observe_tav_quarantine, stat_tav_artifact
from .schema import (
    DEFAULT_QQPCMGR_ROOT,
    DEFAULT_QUARANTINE_DIR,
    DEFAULT_TAV_CACHE_PATH,
    PRODUCT_ID,
    QUARANTINE_CONTAINER_CATEGORY,
    QUARANTINE_ICON_CATEGORY,
    RAW_PRODUCT_NAME,
    TavArtifactMetadata,
    TavQuarantineObservation,
)

METADATA_DIR = f"collection/{PRODUCT_ID}/metadata"
QUARANTINE_OBSERVATION_PATH = f"{METADATA_DIR}/quarantine_observation.json"
NORMALIZED_EVENTS_PATH = f"{METADATA_DIR}/normalized_events.jsonl"
RAW_CONTAINER_REF = f"collection/{PRODUCT_ID}/raw-ref/quarantine_container"
RAW_ICON_REF = f"collection/{PRODUCT_ID}/raw-ref/icon_sidecar"
RAW_TAV_CACHE_REF = f"collection/{PRODUCT_ID}/raw-ref/tav_cache"
WINDOW_TOLERANCE_SECONDS = 5


class TencentPcManagerLogCollector(ProductLogCollector):
    product_id = PRODUCT_ID

    def __init__(self, log_dir: str | Path | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else None

    def collect(
        self,
        workspace: Path,
        case_context: Mapping[str, Any],
        window: CollectionWindow,
    ) -> CollectorResult:
        metadata_dir = workspace / "collection" / PRODUCT_ID / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        errors: list[str] = []
        paths = _resolve_product_paths(self.log_dir)

        try:
            quarantine_exists = paths.quarantine_dir.is_dir()
        except OSError as exc:
            errors.append(f"TAV quarantine directory stat failed: {type(exc).__name__}")
            return _failed_result(
                window=window,
                reason="product_quarantine_dir_stat_failed",
                errors=errors,
                artifacts=_legacy_artifacts(paths=paths, warnings=warnings),
            )
        if not quarantine_exists:
            return _not_collected_result(window, paths)

        sample_md5 = str(case_context.get("sample_md5") or "").strip().casefold()
        if not sample_md5:
            warnings.append("sample_md5_missing_for_tav_quarantine_collection")
            return _unknown_without_observation_result(
                workspace=workspace,
                paths=paths,
                window=window,
                reason="sample_md5_missing_for_tav_quarantine_collection",
                warnings=warnings,
            )
        try:
            sample_md5 = normalize_tav_md5(sample_md5)
        except ValueError:
            warnings.append("sample_md5_invalid_for_tav_quarantine_collection")
            return _unknown_without_observation_result(
                workspace=workspace,
                paths=paths,
                window=window,
                reason="sample_md5_invalid_for_tav_quarantine_collection",
                warnings=warnings,
            )

        original_size = _coerce_int(case_context.get("sample_size"))
        readiness_baseline = _read_tav_readiness_baseline(workspace)
        if not readiness_baseline:
            warnings.append("tav_quarantine_readiness_baseline_missing")

        observation = observe_tav_quarantine(
            paths.quarantine_dir,
            sample_md5,
            original_sample_size=original_size,
        )
        tav_cache = stat_tav_artifact(
            paths.tav_cache_path,
            kind="tav_cache_collection",
        )
        stat_errors = _stat_errors(observation, tav_cache)
        if stat_errors:
            return _failed_result(
                window=window,
                reason="product_metadata_stat_failed",
                errors=stat_errors,
                artifacts=_legacy_artifacts(
                    paths=paths,
                    observation=observation,
                    tav_cache=tav_cache,
                    readiness_baseline=readiness_baseline,
                    warnings=warnings,
                ),
            )

        tav_cache_activity = _tav_cache_activity(readiness_baseline, tav_cache, window)
        warnings.extend(tav_cache_activity.warnings)
        attribution = _attribute_observation(
            observation=observation,
            readiness_baseline=readiness_baseline,
            tav_cache_activity=tav_cache_activity,
            window=window,
        )
        warnings.extend(attribution.warnings)
        events = _normalized_events(
            observation=observation,
            tav_cache=tav_cache,
            tav_cache_activity=tav_cache_activity,
            attribution=attribution,
            case_context=case_context,
            window=window,
        )
        _write_metadata(
            metadata_dir=metadata_dir,
            observation=observation,
            tav_cache=tav_cache,
            tav_cache_activity=tav_cache_activity,
            attribution=attribution,
            events=events,
            readiness_baseline=readiness_baseline,
            warnings=warnings,
        )

        verdict, intercepted, reason = _verdict_from_attribution(
            attribution,
            events,
        )
        return CollectorResult(
            product_id=PRODUCT_ID,
            collection_state="collected",
            verdict=verdict,
            intercepted=intercepted,
            reason=reason,
            evidence_count=len(events),
            events=tuple(events),
            errors=(),
            artifacts=_legacy_artifacts(
                paths=paths,
                observation=observation,
                tav_cache=tav_cache,
                tav_cache_activity=tav_cache_activity,
                attribution=attribution,
                readiness_baseline=readiness_baseline,
                warnings=warnings,
            ),
            artifact_items=_tencent_pc_manager_artifacts(),
            window=window,
            collected_at_utc=_utc_now(),
        )


@dataclass(frozen=True)
class _TencentPcManagerPaths:
    root: Path
    quarantine_dir: Path
    tav_cache_path: Path


@dataclass(frozen=True)
class _TavCacheActivity:
    changed: bool
    time_relation: str
    baseline_present: bool
    current_present: bool
    matched_on: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "time_relation": self.time_relation,
            "baseline_present": self.baseline_present,
            "current_present": self.current_present,
            "matched_on": list(self.matched_on),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class _TavAttribution:
    level: str
    verdict_signal: str
    matched_on: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "TAV log content is encrypted and not parsed",
        "quarantine container content is not read or decrypted",
        "original path and threat name are unavailable in this MVP",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "verdict_signal": self.verdict_signal,
            "matched_on": list(self.matched_on),
            "warnings": list(self.warnings),
            "missing_fields": list(self.missing_fields),
            "limitations": list(self.limitations),
        }


def _resolve_product_paths(configured: Path | None) -> _TencentPcManagerPaths:
    if configured is None:
        return _TencentPcManagerPaths(
            root=Path(DEFAULT_QQPCMGR_ROOT),
            quarantine_dir=Path(DEFAULT_QUARANTINE_DIR),
            tav_cache_path=Path(DEFAULT_TAV_CACHE_PATH),
        )
    configured_path = Path(configured)
    if configured_path.name.casefold() == "quarantine":
        root = configured_path.parent
        quarantine_dir = configured_path
    else:
        root = configured_path
        quarantine_dir = configured_path / "Quarantine"
    return _TencentPcManagerPaths(
        root=root,
        quarantine_dir=quarantine_dir,
        tav_cache_path=root / "TAVWfsDB" / "TAVCacheFullEx.db",
    )


def _read_tav_readiness_baseline(workspace: Path) -> dict[str, Any]:
    readiness = _read_json(workspace / "case_security_product_readiness.json")
    checks = readiness.get("checks")
    if not isinstance(checks, list):
        return {}
    for item in checks:
        if not isinstance(item, Mapping):
            continue
        if item.get("name") != "tav_quarantine_baseline_recorded":
            continue
        data = item.get("data")
        return dict(data) if isinstance(data, Mapping) else {}
    return {}


def _attribute_observation(
    observation: TavQuarantineObservation,
    readiness_baseline: Mapping[str, Any],
    tav_cache_activity: _TavCacheActivity,
    window: CollectionWindow,
) -> _TavAttribution:
    warnings: list[str] = []
    missing_fields = ["threat_name", "original_path"]
    if not observation.container.present:
        if tav_cache_activity.changed:
            return _TavAttribution(
                level="weak",
                verdict_signal="activity_observed",
                matched_on=tav_cache_activity.matched_on,
                warnings=tav_cache_activity.warnings,
                missing_fields=("md5_quarantine_filename", *missing_fields),
            )
        if observation.icon_sidecar.present:
            return _TavAttribution(
                level="weak",
                verdict_signal="unknown",
                matched_on=("icon_sidecar",),
                warnings=("icon_sidecar_without_container",),
                missing_fields=("md5_quarantine_filename", *missing_fields),
            )
        return _TavAttribution(
            level="unattributed",
            verdict_signal="unknown",
            matched_on=(),
            missing_fields=("md5_quarantine_filename", *missing_fields),
        )

    baseline_state = _baseline_state_with_observation(
        readiness_baseline,
        observation,
    )
    if baseline_state == "unchanged":
        return _TavAttribution(
            level="unattributed",
            verdict_signal="unknown",
            matched_on=("md5_quarantine_filename",),
            warnings=("quarantine_container_pre_existing_unchanged",),
            missing_fields=tuple(missing_fields),
        )

    matched_on = ["md5_quarantine_filename"]
    time_relation = _timestamp_relation(observation.container.mtime_utc, window)
    if time_relation == "inside_window":
        matched_on.append("time_window")
    elif time_relation == "near_window":
        matched_on.append("time_window_tolerance")
        warnings.append("mtime_near_case_window")
    elif time_relation == "unknown":
        warnings.append("quarantine_container_mtime_unavailable")
    else:
        return _TavAttribution(
            level="weak",
            verdict_signal="unknown",
            matched_on=tuple(matched_on),
            warnings=("quarantine_container_mtime_outside_case_window",),
            missing_fields=tuple(missing_fields),
        )

    size_level = observation.size_delta.level
    if size_level == "strong":
        matched_on.append("size_delta")
    elif size_level == "medium":
        matched_on.append("size_delta_medium")
        warnings.extend(observation.size_delta.warnings)
    elif size_level == "weak":
        warnings.extend(observation.size_delta.warnings)
    else:
        warnings.extend(observation.size_delta.warnings)

    if observation.icon_sidecar.present:
        matched_on.append("icon_sidecar")
    if baseline_state == "changed":
        matched_on.append("baseline_changed")

    level = _container_attribution_level(
        baseline_state=baseline_state,
        time_relation=time_relation,
        size_level=size_level,
    )
    return _TavAttribution(
        level=level,
        verdict_signal="intercepted",
        matched_on=tuple(dict.fromkeys(matched_on)),
        warnings=tuple(dict.fromkeys(warnings)),
        missing_fields=tuple(missing_fields),
    )


def _container_attribution_level(
    baseline_state: str,
    time_relation: str,
    size_level: str,
) -> str:
    if (
        baseline_state in {"new", "changed", "missing"}
        and time_relation == "inside_window"
        and size_level == "strong"
    ):
        return "strong"
    return "medium"


def _baseline_container_state(readiness_baseline: Mapping[str, Any]) -> str:
    if not readiness_baseline:
        return "missing"
    baseline_container = readiness_baseline.get("quarantine_container")
    if not isinstance(baseline_container, Mapping):
        return "missing"
    if not bool(baseline_container.get("present")):
        return "new"
    return "baseline_present"


def _baseline_state_with_observation(
    readiness_baseline: Mapping[str, Any],
    observation: TavQuarantineObservation,
) -> str:
    state = _baseline_container_state(readiness_baseline)
    if state != "baseline_present":
        return state
    baseline_container = readiness_baseline.get("quarantine_container")
    baseline_container = (
        baseline_container if isinstance(baseline_container, Mapping) else {}
    )
    baseline_size = _coerce_int(baseline_container.get("size"))
    baseline_mtime = str(baseline_container.get("mtime_utc", ""))
    if (
        baseline_size == observation.container.size
        and baseline_mtime == observation.container.mtime_utc
    ):
        return "unchanged"
    return "changed"


def _tav_cache_activity(
    readiness_baseline: Mapping[str, Any],
    current: TavArtifactMetadata,
    window: CollectionWindow,
) -> _TavCacheActivity:
    warnings: list[str] = []
    baseline = readiness_baseline.get("tav_cache")
    baseline = baseline if isinstance(baseline, Mapping) else {}
    baseline_present = bool(baseline.get("present"))
    current_present = current.present
    if not current_present:
        return _TavCacheActivity(
            changed=False,
            time_relation="unknown",
            baseline_present=baseline_present,
            current_present=False,
            matched_on=(),
            warnings=("tav_cache_missing",),
        )
    changed = baseline_present and (
        _coerce_int(baseline.get("size")) != current.size
        or str(baseline.get("mtime_utc", "")) != current.mtime_utc
    )
    relation = _timestamp_relation(current.mtime_utc, window)
    matched_on: list[str] = []
    if changed:
        matched_on.append("tav_cache_mtime_or_size_changed")
        if relation == "inside_window":
            matched_on.append("time_window")
        elif relation == "near_window":
            matched_on.append("time_window_tolerance")
            warnings.append("mtime_near_case_window")
        elif relation == "outside_window":
            warnings.append("tav_cache_mtime_outside_case_window")
    effective_changed = changed and relation in {
        "inside_window",
        "near_window",
        "unknown",
    }
    return _TavCacheActivity(
        changed=effective_changed,
        time_relation=relation,
        baseline_present=baseline_present,
        current_present=current_present,
        matched_on=tuple(matched_on if effective_changed else ()),
        warnings=tuple(warnings),
    )


def _normalized_events(
    observation: TavQuarantineObservation,
    tav_cache: TavArtifactMetadata,
    tav_cache_activity: _TavCacheActivity,
    attribution: _TavAttribution,
    case_context: Mapping[str, Any],
    window: CollectionWindow,
) -> list[NormalizedSecurityEvent]:
    if observation.container.present:
        return [
            _quarantine_event(
                observation=observation,
                tav_cache_activity=tav_cache_activity,
                attribution=attribution,
                case_context=case_context,
                window=window,
            )
        ]
    if tav_cache_activity.changed:
        return [
            _activity_event(
                tav_cache=tav_cache,
                tav_cache_activity=tav_cache_activity,
                attribution=attribution,
                case_context=case_context,
                window=window,
            )
        ]
    if observation.icon_sidecar.present:
        return [
            _icon_sidecar_event(
                observation=observation,
                attribution=attribution,
                case_context=case_context,
                window=window,
            )
        ]
    return []


def _quarantine_event(
    observation: TavQuarantineObservation,
    tav_cache_activity: _TavCacheActivity,
    attribution: _TavAttribution,
    case_context: Mapping[str, Any],
    window: CollectionWindow,
) -> NormalizedSecurityEvent:
    evidence = {
        "raw_product": RAW_PRODUCT_NAME,
        "product_action": "quarantined"
        if attribution.verdict_signal == "intercepted"
        else "unknown",
        "case_relevant": attribution.verdict_signal == "intercepted",
        "threat_name": "",
        "file_path": "",
        "original_path_known": False,
        "quarantine_ref": f"<tav_quarantine>/{observation.sample_md5}",
        "hashes": {
            "md5": observation.sample_md5,
            "sha256": str(case_context.get("sample_sha256", "")),
        },
        "file_size": _coerce_int(case_context.get("sample_size")),
        "quarantine_container_size": observation.container.size,
        "container_size_delta": observation.size_delta.delta,
        "icon_sidecar_present": observation.icon_sidecar.present,
        "tav_cache_mtime_changed": tav_cache_activity.changed,
        "attribution": attribution.to_dict(),
    }
    return NormalizedSecurityEvent(
        timestamp_utc=observation.container.mtime_utc
        or window.collection_started_at_utc,
        source="tencent_pc_manager_quarantine_metadata",
        event_type="av_quarantined"
        if attribution.verdict_signal == "intercepted"
        else "av_quarantine_container_observed",
        case_id=str(case_context.get("case_id", "")),
        sample_id=str(case_context.get("sample_id", "")),
        product_id=PRODUCT_ID,
        confidence=_confidence_for_level(attribution.level),
        message="Tencent PC Manager quarantine metadata observed for current sample",
        evidence=evidence,
        raw_ref=QUARANTINE_OBSERVATION_PATH,
    )


def _activity_event(
    tav_cache: TavArtifactMetadata,
    tav_cache_activity: _TavCacheActivity,
    attribution: _TavAttribution,
    case_context: Mapping[str, Any],
    window: CollectionWindow,
) -> NormalizedSecurityEvent:
    evidence = {
        "raw_product": RAW_PRODUCT_NAME,
        "product_action": "unknown",
        "case_relevant": False,
        "tav_cache_present": tav_cache.present,
        "tav_cache_size": tav_cache.size,
        "tav_cache_mtime_utc": tav_cache.mtime_utc,
        "tav_cache_activity": tav_cache_activity.to_dict(),
        "attribution": attribution.to_dict(),
    }
    return NormalizedSecurityEvent(
        timestamp_utc=tav_cache.mtime_utc or window.collection_started_at_utc,
        source="tencent_pc_manager_quarantine_metadata",
        event_type="product_log_activity_observed",
        case_id=str(case_context.get("case_id", "")),
        sample_id=str(case_context.get("sample_id", "")),
        product_id=PRODUCT_ID,
        confidence="low",
        message="Tencent PC Manager metadata activity observed without case container",
        evidence=evidence,
        raw_ref=QUARANTINE_OBSERVATION_PATH,
    )


def _icon_sidecar_event(
    observation: TavQuarantineObservation,
    attribution: _TavAttribution,
    case_context: Mapping[str, Any],
    window: CollectionWindow,
) -> NormalizedSecurityEvent:
    evidence = {
        "raw_product": RAW_PRODUCT_NAME,
        "product_action": "unknown",
        "case_relevant": False,
        "quarantine_ref": f"<tav_quarantine>/{observation.sample_md5}.ico",
        "icon_sidecar_present": True,
        "attribution": attribution.to_dict(),
    }
    return NormalizedSecurityEvent(
        timestamp_utc=observation.icon_sidecar.mtime_utc
        or window.collection_started_at_utc,
        source="tencent_pc_manager_quarantine_metadata",
        event_type="quarantine_icon_sidecar_observed",
        case_id=str(case_context.get("case_id", "")),
        sample_id=str(case_context.get("sample_id", "")),
        product_id=PRODUCT_ID,
        confidence="low",
        message="Tencent PC Manager icon sidecar observed without quarantine container",
        evidence=evidence,
        raw_ref=QUARANTINE_OBSERVATION_PATH,
    )


def _verdict_from_attribution(
    attribution: _TavAttribution,
    events: Sequence[NormalizedSecurityEvent],
) -> tuple[str, bool | None, str]:
    if attribution.verdict_signal == "intercepted" and attribution.level in {
        "strong",
        "medium",
    }:
        return "intercepted", True, "tav_quarantine_container_matched_case_md5"
    if events:
        return "unknown", None, "no_confident_tav_quarantine_evidence_for_case"
    return "unknown", None, "no_relevant_tav_quarantine_evidence_for_case"


def _write_metadata(
    metadata_dir: Path,
    observation: TavQuarantineObservation,
    tav_cache: TavArtifactMetadata,
    tav_cache_activity: _TavCacheActivity,
    attribution: _TavAttribution,
    events: Sequence[NormalizedSecurityEvent],
    readiness_baseline: Mapping[str, Any],
    warnings: Sequence[str],
) -> None:
    payload = {
        "schema_version": "tencent-pc-manager-quarantine-observation.v1",
        "product_id": PRODUCT_ID,
        "raw_product": RAW_PRODUCT_NAME,
        "observation": observation.to_dict(),
        "tav_cache": tav_cache.to_dict(),
        "tav_cache_activity": tav_cache_activity.to_dict(),
        "attribution": attribution.to_dict(),
        "readiness_baseline_present": bool(readiness_baseline),
        "warnings": list(dict.fromkeys(warnings)),
        "raw_artifacts_copied": False,
    }
    (metadata_dir / "quarantine_observation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (metadata_dir / "normalized_events.jsonl").write_text(
        "".join(
            json.dumps(event.to_dict(), ensure_ascii=False) + "\n" for event in events
        ),
        encoding="utf-8",
    )


def _not_collected_result(
    window: CollectionWindow,
    paths: _TencentPcManagerPaths,
) -> CollectorResult:
    return CollectorResult(
        product_id=PRODUCT_ID,
        collection_state="not_collected",
        verdict="unknown",
        intercepted=None,
        reason="product_quarantine_dir_not_found",
        evidence_count=0,
        events=(),
        errors=(),
        artifacts=_legacy_artifacts(paths=paths, warnings=()),
        artifact_items=_tencent_pc_manager_artifacts(),
        window=window,
        collected_at_utc=_utc_now(),
    )


def _unknown_without_observation_result(
    workspace: Path,
    paths: _TencentPcManagerPaths,
    window: CollectionWindow,
    reason: str,
    warnings: Sequence[str],
) -> CollectorResult:
    metadata_dir = workspace / "collection" / PRODUCT_ID / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "quarantine_observation.json").write_text(
        json.dumps(
            {
                "schema_version": "tencent-pc-manager-quarantine-observation.v1",
                "product_id": PRODUCT_ID,
                "raw_product": RAW_PRODUCT_NAME,
                "warnings": list(dict.fromkeys(warnings)),
                "raw_artifacts_copied": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return CollectorResult(
        product_id=PRODUCT_ID,
        collection_state="collected",
        verdict="unknown",
        intercepted=None,
        reason=reason,
        evidence_count=0,
        events=(),
        errors=(),
        artifacts=_legacy_artifacts(paths=paths, warnings=warnings),
        artifact_items=_tencent_pc_manager_artifacts(),
        window=window,
        collected_at_utc=_utc_now(),
    )


def _failed_result(
    window: CollectionWindow,
    reason: str,
    errors: Sequence[str],
    artifacts: Mapping[str, Any],
) -> CollectorResult:
    return CollectorResult(
        product_id=PRODUCT_ID,
        collection_state="failed",
        verdict="failed",
        intercepted=None,
        reason=reason,
        evidence_count=0,
        events=(),
        errors=tuple(errors),
        artifacts=artifacts,
        artifact_items=_tencent_pc_manager_artifacts(),
        window=window,
        collected_at_utc=_utc_now(),
    )


def _legacy_artifacts(
    paths: _TencentPcManagerPaths,
    observation: TavQuarantineObservation | None = None,
    tav_cache: TavArtifactMetadata | None = None,
    tav_cache_activity: _TavCacheActivity | None = None,
    attribution: _TavAttribution | None = None,
    readiness_baseline: Mapping[str, Any] | None = None,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "source_product": RAW_PRODUCT_NAME,
        "quarantine_dir": str(paths.quarantine_dir),
        "tav_cache_path": str(paths.tav_cache_path),
        "quarantine_observation": observation.to_dict() if observation else {},
        "tav_cache": tav_cache.to_dict() if tav_cache else {},
        "tav_cache_activity": tav_cache_activity.to_dict()
        if tav_cache_activity
        else {},
        "attribution": attribution.to_dict() if attribution else {},
        "readiness_baseline_present": bool(readiness_baseline),
        "metadata_paths": {
            "quarantine_observation": QUARANTINE_OBSERVATION_PATH,
            "normalized_events": NORMALIZED_EVENTS_PATH,
        },
        "warnings": tuple(dict.fromkeys(warnings)),
    }


def _tencent_pc_manager_artifacts() -> tuple[CollectorArtifact, ...]:
    return (
        CollectorArtifact(
            path=QUARANTINE_OBSERVATION_PATH,
            category="collector_metadata",
            include_in_evidence=True,
            redaction_owner="exporter",
            redaction_state="redacted",
            sensitivity="low",
            reason="derived Tencent PC Manager quarantine metadata observation",
        ),
        CollectorArtifact(
            path=NORMALIZED_EVENTS_PATH,
            category="normalized_evidence",
            include_in_evidence=True,
            redaction_owner="exporter",
            redaction_state="redacted",
            sensitivity="low",
            reason="derived Tencent PC Manager normalized evidence",
        ),
        CollectorArtifact(
            path=RAW_CONTAINER_REF,
            category=QUARANTINE_CONTAINER_CATEGORY,
            include_in_evidence=False,
            redaction_owner="collector",
            redaction_state="raw_blocked",
            sensitivity="high",
            reason="TAV quarantine container is raw product artifact and is excluded",
        ),
        CollectorArtifact(
            path=RAW_ICON_REF,
            category=QUARANTINE_ICON_CATEGORY,
            include_in_evidence=False,
            redaction_owner="collector",
            redaction_state="raw_blocked",
            sensitivity="medium",
            reason="TAV icon sidecar is raw product artifact and is excluded",
        ),
        CollectorArtifact(
            path=RAW_TAV_CACHE_REF,
            category="raw_product_log",
            include_in_evidence=False,
            redaction_owner="collector",
            redaction_state="raw_blocked",
            sensitivity="high",
            reason="TAVCacheFullEx.db is encrypted/private raw product metadata",
        ),
    )


def _stat_errors(
    observation: TavQuarantineObservation,
    tav_cache: TavArtifactMetadata,
) -> list[str]:
    errors: list[str] = []
    for item in (observation.container, observation.icon_sidecar, tav_cache):
        if item.stat_error:
            errors.append(f"{item.kind} stat failed: {item.stat_error}")
    return errors


def _timestamp_relation(value: str, window: CollectionWindow) -> str:
    timestamp = _parse_utc(value)
    if timestamp is None:
        return "unknown"
    start = _parse_utc(window.uploaded_at_utc) or _parse_utc(window.start_utc)
    end = (
        _parse_utc(window.collection_finished_at_utc)
        or _parse_utc(window.end_utc)
        or _parse_utc(window.collection_started_at_utc)
    )
    if start is None or end is None:
        return "unknown"
    if start <= timestamp <= end:
        return "inside_window"
    tolerance = timedelta(seconds=WINDOW_TOLERANCE_SECONDS)
    if start - tolerance <= timestamp <= end + tolerance:
        return "near_window"
    return "outside_window"


def _parse_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _confidence_for_level(level: str) -> str:
    return {"strong": "high", "medium": "medium", "weak": "low"}.get(level, "low")
