from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cloud_av_agent_lab.guest_agent_server.collectors.base import CollectionWindow
from cloud_av_agent_lab.guest_agent_server.collectors.probe import (
    ProductObservationProbe,
    ProductObservationProbeResult,
)
from cloud_av_agent_lab.guest_agent_server.workspace.io import _coerce_int, _utc_now

from .collector import (
    _attribute_observation,
    _read_tav_readiness_baseline,
    _resolve_product_paths,
    _stat_errors,
    _tav_cache_activity,
)
from .quarantine import normalize_tav_md5, observe_tav_quarantine, stat_tav_artifact
from .schema import PRODUCT_ID


class TencentPcManagerObservationProbe(ProductObservationProbe):
    product_id = PRODUCT_ID

    def __init__(self, log_dir: str | Path | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else None

    def probe(
        self,
        workspace: Path,
        case_context: Mapping[str, Any],
        window: CollectionWindow,
    ) -> ProductObservationProbeResult:
        paths = _resolve_product_paths(self.log_dir)
        try:
            quarantine_exists = paths.quarantine_dir.is_dir()
        except OSError as exc:
            return _probe_failed(
                "product_quarantine_dir_stat_failed",
                error_type=type(exc).__name__,
                window=window,
            )
        if not quarantine_exists:
            return _no_signal(
                "product_quarantine_dir_not_found",
                window=window,
            )

        sample_md5 = str(case_context.get("sample_md5") or "").strip().casefold()
        if not sample_md5:
            return _probe_failed(
                "sample_md5_missing_for_tav_quarantine_probe",
                window=window,
            )
        try:
            sample_md5 = normalize_tav_md5(sample_md5)
        except ValueError:
            return _probe_failed(
                "sample_md5_invalid_for_tav_quarantine_probe",
                window=window,
            )

        observation = observe_tav_quarantine(
            paths.quarantine_dir,
            sample_md5,
            original_sample_size=_coerce_int(case_context.get("sample_size")),
        )
        tav_cache = stat_tav_artifact(paths.tav_cache_path, kind="tav_cache_probe")
        stat_errors = _stat_errors(observation, tav_cache)
        if stat_errors:
            return _probe_failed(
                "product_metadata_stat_failed",
                errors=stat_errors,
                window=window,
            )

        readiness_baseline = _read_tav_readiness_baseline(workspace)
        tav_cache_activity = _tav_cache_activity(readiness_baseline, tav_cache, window)
        attribution = _attribute_observation(
            observation=observation,
            readiness_baseline=readiness_baseline,
            tav_cache_activity=tav_cache_activity,
            window=window,
        )

        state = _probe_state(
            container_present=observation.container.present,
            icon_present=observation.icon_sidecar.present,
            tav_cache_changed=tav_cache_activity.changed,
            attribution_level=attribution.level,
            verdict_signal=attribution.verdict_signal,
        )
        observed = state != "no_signal"
        reason_codes = _reason_codes(
            probe_state=state,
            attribution_level=attribution.level,
            verdict_signal=attribution.verdict_signal,
        )
        return ProductObservationProbeResult(
            product_id=PRODUCT_ID,
            probe_state=state,
            observed=observed,
            attribution_level=attribution.level if observed else "none",
            confidence=_confidence_for_probe_state(state, attribution.level),
            reason_codes=reason_codes,
            evidence_count=1 if observed else 0,
            observed_at_utc=_utc_now(),
            safe_summary={
                "container_present": observation.container.present,
                "icon_sidecar_present": observation.icon_sidecar.present,
                "container_size": observation.container.size,
                "container_mtime_utc": observation.container.mtime_utc,
                "tav_cache_present": tav_cache.present,
                "tav_cache_changed": tav_cache_activity.changed,
                "tav_cache_time_relation": tav_cache_activity.time_relation,
                "matched_on": list(attribution.matched_on),
                "warnings": list(
                    dict.fromkeys(
                        [
                            *observation.warnings,
                            *tav_cache_activity.warnings,
                            *attribution.warnings,
                        ]
                    )
                ),
                "window": window.to_dict(),
            },
        )


def _probe_state(
    *,
    container_present: bool,
    icon_present: bool,
    tav_cache_changed: bool,
    attribution_level: str,
    verdict_signal: str,
) -> str:
    if verdict_signal == "intercepted" and attribution_level == "strong":
        return "strong_signal_observed"
    if (
        container_present
        or icon_present
        or (verdict_signal == "intercepted" and attribution_level == "medium")
    ):
        return "case_relevant_signal_observed"
    if tav_cache_changed:
        return "activity_observed"
    return "no_signal"


def _reason_codes(
    *,
    probe_state: str,
    attribution_level: str,
    verdict_signal: str,
) -> tuple[str, ...]:
    if probe_state == "strong_signal_observed":
        return ("tav_quarantine_container_strong_signal",)
    if probe_state == "case_relevant_signal_observed":
        if verdict_signal == "intercepted":
            return ("tav_quarantine_container_case_relevant_signal",)
        return ("tav_quarantine_metadata_case_relevant_signal",)
    if probe_state == "activity_observed":
        return ("tav_cache_activity_observed",)
    if attribution_level == "unattributed":
        return ("tav_metadata_unattributed",)
    return ("no_tav_quarantine_signal_observed",)


def _confidence_for_probe_state(probe_state: str, attribution_level: str) -> str:
    if probe_state == "strong_signal_observed":
        return "high"
    if probe_state == "case_relevant_signal_observed":
        return {"medium": "medium", "weak": "low"}.get(attribution_level, "low")
    if probe_state == "activity_observed":
        return "low"
    return "low"


def _no_signal(reason: str, window: CollectionWindow) -> ProductObservationProbeResult:
    return ProductObservationProbeResult(
        product_id=PRODUCT_ID,
        probe_state="no_signal",
        observed=False,
        reason_codes=(reason,),
        observed_at_utc=_utc_now(),
        safe_summary={"window": window.to_dict()},
    )


def _probe_failed(
    reason: str,
    *,
    window: CollectionWindow,
    error_type: str = "",
    errors: list[str] | None = None,
) -> ProductObservationProbeResult:
    summary: dict[str, Any] = {"window": window.to_dict()}
    if error_type:
        summary["error_type"] = error_type
    if errors:
        summary["errors"] = list(errors)
    return ProductObservationProbeResult(
        product_id=PRODUCT_ID,
        probe_state="probe_failed",
        observed=False,
        reason_codes=(reason,),
        observed_at_utc=_utc_now(),
        safe_summary=summary,
    )
