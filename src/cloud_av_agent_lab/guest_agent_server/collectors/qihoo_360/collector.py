from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
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

from .attribution import attribute_qihoo360_event
from .baseline import Qihoo360SummaryBaseline, filter_qihoo360_delta_events
from .schema import (
    PRODUCT_ID,
    PRODUCT_LOG_SOURCE,
    RAW_PRODUCT_NAME,
    SUMMARY_DATABASE_NAME,
    UNION_METADATA_NAME,
    Qihoo360ParsedEvent,
)
from .sqlite_reader import Qihoo360SQLiteError, read_qihoo360_summary_database

CONFIDENT_ATTRIBUTIONS = {"strong", "medium"}
QUARANTINE_PREFIX = r"c:\$360section"


class Qihoo360LogCollector(ProductLogCollector):
    product_id = PRODUCT_ID
    DEFAULT_USERS_ROOT = Path(r"C:\Users")

    def __init__(self, log_dir: str | Path | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else None

    def collect(
        self,
        workspace: Path,
        case_context: Mapping[str, Any],
        window: CollectionWindow,
    ) -> CollectorResult:
        artifact_dir = workspace / "collection" / PRODUCT_ID / "raw"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        errors: list[str] = []

        summary_path = _resolve_summary_path(self.log_dir, self.DEFAULT_USERS_ROOT)
        if summary_path is None:
            return _not_collected_result(window)

        summary_copy, snapshot = _copy_snapshot(
            source=summary_path,
            artifact_dir=artifact_dir,
            warnings=warnings,
            errors=errors,
        )
        union_snapshot = _copy_optional_union(
            summary_path=summary_path,
            artifact_dir=artifact_dir,
            warnings=warnings,
        )
        if summary_copy is None:
            return _failed_result(
                window=window,
                reason="summary_dat_snapshot_failed",
                errors=errors,
                artifacts=_legacy_artifacts(snapshot, union_snapshot),
            )

        try:
            summary = read_qihoo360_summary_database(summary_copy)
        except Qihoo360SQLiteError as exc:
            errors.append(f"failed to read 360safe Summary.dat: {_safe_error(exc)}")
            return _failed_result(
                window=window,
                reason="summary_dat_parse_failed",
                errors=errors,
                artifacts=_legacy_artifacts(snapshot, union_snapshot),
            )

        baseline = _baseline_from_context(case_context)
        delta_ids: tuple[int, ...] = ()
        delta_payload: dict[str, Any] = {}
        candidate_events: Sequence[Qihoo360ParsedEvent] = summary.events
        if baseline is not None:
            delta_events, delta = filter_qihoo360_delta_events(summary, baseline)
            delta_ids = tuple(delta.candidate_fq_ids)
            delta_payload = delta.to_dict()
            warnings.extend(delta.warnings)
            if delta.baseline_delta_usable:
                candidate_events = delta_events

        events = [
            _normalize_event(
                event=event,
                case_context=case_context,
                window=window,
                baseline_delta_ids=delta_ids,
            )
            for event in candidate_events
        ]
        events = [event for event in events if event is not None]

        verdict, intercepted, reason = _verdict_from_events(events, warnings)
        return CollectorResult(
            product_id=PRODUCT_ID,
            collection_state="collected",
            verdict=verdict,
            intercepted=intercepted,
            reason=reason,
            evidence_count=len(events),
            events=tuple(events),
            errors=(),
            artifacts={
                "source_product": RAW_PRODUCT_NAME,
                "summary_snapshot": snapshot,
                "union_snapshot": union_snapshot,
                "delta_filter": delta_payload,
                "warnings": tuple(dict.fromkeys(warnings)),
            },
            artifact_items=_qihoo360_artifacts(
                summary_copied=True,
                union_snapshot=union_snapshot,
            ),
            window=window,
            collected_at_utc=_utc_now(),
        )


def _normalize_event(
    event: Qihoo360ParsedEvent,
    case_context: Mapping[str, Any],
    window: CollectionWindow,
    baseline_delta_ids: Sequence[int],
) -> NormalizedSecurityEvent | None:
    attribution = attribute_qihoo360_event(
        event,
        case_context,
        window=window,
        baseline_delta_ids=baseline_delta_ids,
    )
    if not (
        event.threat_name or event.file_path or event.sha256 or event.quarantine_path
    ):
        return None
    evidence = {
        "source_table": "FQ",
        "source_row_id": event.source_row_id,
        "event_source": event.event_source,
        "raw_product": event.raw_product or RAW_PRODUCT_NAME,
        "threat_name": event.threat_name,
        "threat_category": event.threat_category,
        "raw_action_text": event.raw_action_text,
        "event_time_raw": event.event_time_raw,
        "time_confidence": event.time_confidence,
        "file_path": event.file_path,
        "quarantine_path": event.quarantine_path,
        "quarantine_path_present": bool(event.quarantine_path),
        "file_size": event.file_size,
        "md5": event.md5,
        "sha1": event.sha1,
        "sha256": event.sha256,
        "raw_category_hint": event.raw_category_hint,
        "related_paths": list(event.related_paths),
        "attribution": attribution.level,
        "matched_on": list(attribution.matched_on),
        "attribution_warnings": list(attribution.warnings),
        "path_matched": attribution.path_matched,
        "sha256_matched": attribution.sha256_matched,
        "baseline_delta_matched": attribution.baseline_delta_matched,
        "time_window_matched": attribution.time_window_matched,
        "parse_warnings": list(event.parse_warnings),
        "action_semantics": _action_semantics(event, attribution.level),
    }
    event_type = _event_type(event)
    return NormalizedSecurityEvent(
        timestamp_utc=event.observed_at_utc or window.collection_started_at_utc,
        source=PRODUCT_LOG_SOURCE,
        event_type=event_type,
        case_id=str(case_context.get("case_id", "")),
        sample_id=str(case_context.get("sample_id", "")),
        product_id=PRODUCT_ID,
        confidence=_confidence_for_attribution(attribution.level),
        message=_message_for_event(event),
        evidence=evidence,
        raw_ref=f"{SUMMARY_DATABASE_NAME}:FQ#{event.source_row_id or ''}",
    )


def _verdict_from_events(
    events: Sequence[NormalizedSecurityEvent],
    warnings: Sequence[str],
) -> tuple[str, bool | None, str]:
    confident_events = [
        event
        for event in events
        if str(event.evidence.get("attribution")) in CONFIDENT_ATTRIBUTIONS
    ]
    intercepted_events = [
        event
        for event in confident_events
        if _is_quarantine_path(str(event.evidence.get("quarantine_path") or ""))
    ]
    if intercepted_events:
        return "intercepted", True, "qihoo360_quarantine_evidence_matched_case"
    detected_events = [
        event
        for event in confident_events
        if str(event.evidence.get("threat_name") or "")
    ]
    if detected_events:
        return "detected", False, "qihoo360_detection_evidence_matched_case"
    if events:
        return "unknown", None, "no_relevant_qihoo360_events_for_case"
    if warnings:
        return "unknown", None, "no_relevant_qihoo360_events_for_case"
    return "unknown", None, "no_relevant_qihoo360_events_for_case"


def _not_collected_result(window: CollectionWindow) -> CollectorResult:
    return CollectorResult(
        product_id=PRODUCT_ID,
        collection_state="not_collected",
        verdict="unknown",
        intercepted=None,
        reason="product_log_not_found",
        evidence_count=0,
        events=(),
        errors=(),
        artifacts={"source_product": RAW_PRODUCT_NAME, "summary_snapshot": {}},
        artifact_items=_qihoo360_artifacts(summary_copied=False, union_snapshot={}),
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
        artifact_items=_qihoo360_artifacts(
            summary_copied=bool(artifacts.get("summary_snapshot", {}).get("copied")),
            union_snapshot=artifacts.get("union_snapshot", {}),
        ),
        window=window,
        collected_at_utc=_utc_now(),
    )


def _copy_snapshot(
    source: Path,
    artifact_dir: Path,
    warnings: list[str],
    errors: list[str],
) -> tuple[Path | None, dict[str, Any]]:
    destination = artifact_dir / SUMMARY_DATABASE_NAME
    started_at = _utc_now()
    before = _stat_metadata(source)
    snapshot = {
        "source_path": str(source),
        "copied_path": str(destination),
        "relative_copied_path": f"collection/{PRODUCT_ID}/raw/{SUMMARY_DATABASE_NAME}",
        "source_size_before": before.get("size"),
        "source_mtime_before": before.get("mtime_utc"),
        "source_size_after": None,
        "source_mtime_after": "",
        "copy_started_at_utc": started_at,
        "copy_finished_at_utc": "",
        "copied": False,
        "warnings": [],
    }
    if before.get("error"):
        errors.append(f"failed to stat 360safe Summary.dat: {before['error']}")
        snapshot["warnings"] = tuple(snapshot["warnings"])
        return None, snapshot
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        errors.append(f"failed to copy 360safe Summary.dat: {type(exc).__name__}")
        snapshot["copy_finished_at_utc"] = _utc_now()
        snapshot["warnings"] = tuple(snapshot["warnings"])
        return None, snapshot
    after = _stat_metadata(source)
    snapshot["copy_finished_at_utc"] = _utc_now()
    snapshot["source_size_after"] = after.get("size")
    snapshot["source_mtime_after"] = after.get("mtime_utc")
    snapshot["copied"] = True
    if after.get("error"):
        warning = "snapshot_source_stat_after_failed"
        warnings.append(warning)
        snapshot["warnings"].append(warning)
    elif (
        snapshot["source_size_before"] != snapshot["source_size_after"]
        or snapshot["source_mtime_before"] != snapshot["source_mtime_after"]
    ):
        warning = "snapshot_may_be_changing"
        warnings.append(warning)
        snapshot["warnings"].append(warning)
    snapshot["warnings"] = tuple(snapshot["warnings"])
    return destination, snapshot


def _copy_optional_union(
    summary_path: Path,
    artifact_dir: Path,
    warnings: list[str],
) -> dict[str, Any]:
    source = summary_path.with_name(UNION_METADATA_NAME)
    destination = artifact_dir / UNION_METADATA_NAME
    metadata = {
        "source_path": str(source),
        "copied_path": str(destination),
        "relative_copied_path": f"collection/{PRODUCT_ID}/raw/{UNION_METADATA_NAME}",
        "exists": source.is_file(),
        "copied": False,
        "size": None,
        "mtime_utc": "",
        "copied_at_utc": "",
        "warnings": [],
    }
    if not source.is_file():
        metadata["warnings"] = tuple(metadata["warnings"])
        return metadata
    before = _stat_metadata(source)
    metadata["size"] = before.get("size")
    metadata["mtime_utc"] = before.get("mtime_utc", "")
    try:
        shutil.copy2(source, destination)
        metadata["copied"] = True
        metadata["copied_at_utc"] = _utc_now()
    except OSError as exc:
        warning = f"union_metadata_copy_failed:{type(exc).__name__}"
        warnings.append(warning)
        metadata["warnings"].append(warning)
    metadata["warnings"] = tuple(metadata["warnings"])
    return metadata


def _resolve_summary_path(log_dir: Path | None, users_root: Path) -> Path | None:
    candidates: list[Path] = []
    if log_dir is not None:
        if log_dir.name.casefold() == SUMMARY_DATABASE_NAME.casefold():
            candidates.append(log_dir)
        else:
            candidates.append(log_dir / SUMMARY_DATABASE_NAME)
    else:
        candidates.extend(_default_summary_candidates(users_root))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            return candidate
    return None


def _default_summary_candidates(users_root: Path) -> tuple[Path, ...]:
    try:
        user_dirs = [path for path in users_root.iterdir() if path.is_dir()]
    except OSError:
        user_dirs = []
    return tuple(
        user_dir / "AppData" / "Roaming" / "360Quarant" / SUMMARY_DATABASE_NAME
        for user_dir in user_dirs
    )


def _baseline_from_context(
    case_context: Mapping[str, Any],
) -> Qihoo360SummaryBaseline | None:
    baseline = case_context.get("qihoo360_baseline") or case_context.get(
        "qihoo_360_baseline"
    )
    if not isinstance(baseline, Mapping):
        return None
    try:
        known_ids = tuple(int(item) for item in baseline.get("known_fq_ids", ()))
    except (TypeError, ValueError):
        known_ids = ()
    max_fq_id = baseline.get("max_fq_id")
    try:
        max_fq_id = int(max_fq_id) if max_fq_id is not None else None
    except (TypeError, ValueError):
        max_fq_id = None
    return Qihoo360SummaryBaseline(
        source_path=str(baseline.get("source_path", "")),
        summary_dat_size=_coerce_int(baseline.get("summary_dat_size")) or 0,
        summary_dat_mtime_utc=str(baseline.get("summary_dat_mtime_utc", "")),
        max_fq_id=max_fq_id,
        known_fq_ids=known_ids,
    )


def _qihoo360_artifacts(
    summary_copied: bool,
    union_snapshot: Mapping[str, Any],
) -> tuple[CollectorArtifact, ...]:
    return (
        CollectorArtifact(
            path=f"collection/{PRODUCT_ID}/raw/{SUMMARY_DATABASE_NAME}",
            category="raw_product_log",
            include_in_evidence=False,
            redaction_owner="collector",
            redaction_state="raw_blocked",
            sensitivity="high",
            reason=(
                "raw Qihoo 360 SQLite snapshot is not included in the default "
                "redacted evidence bundle"
                if summary_copied
                else "raw Qihoo 360 SQLite snapshot was not copied"
            ),
        ),
        CollectorArtifact(
            path=f"collection/{PRODUCT_ID}/raw/{UNION_METADATA_NAME}",
            category="raw_product_log",
            include_in_evidence=False,
            redaction_owner="collector",
            redaction_state="raw_blocked",
            sensitivity="high",
            reason=(
                "raw Qihoo 360 union metadata is not included in the default "
                "redacted evidence bundle"
                if bool(union_snapshot.get("copied"))
                else "raw Qihoo 360 union metadata was not copied"
            ),
        ),
        CollectorArtifact(
            path="collector/normalized_evidence.json",
            category="normalized_evidence",
            include_in_evidence=True,
            redaction_owner="exporter",
            redaction_state="redacted",
            sensitivity="low",
            reason="derived normalized Qihoo 360 evidence with exporter text redaction",
        ),
    )


def _legacy_artifacts(
    snapshot: Mapping[str, Any],
    union_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "source_product": RAW_PRODUCT_NAME,
        "summary_snapshot": dict(snapshot),
        "union_snapshot": dict(union_snapshot),
    }


def _event_type(event: Qihoo360ParsedEvent) -> str:
    if event.threat_name and _is_quarantine_path(event.quarantine_path):
        return "av_quarantined"
    if event.threat_name:
        return "av_detected"
    if event.raw_action_text or event.quarantine_path:
        return "av_quarantine_summary"
    return "av_action_unknown"


def _action_semantics(event: Qihoo360ParsedEvent, attribution: str) -> str:
    if _is_quarantine_path(event.quarantine_path):
        return "quarantine_path_present"
    if event.threat_name and attribution in CONFIDENT_ATTRIBUTIONS:
        return "detected_without_confirmed_action"
    if event.raw_action_text:
        return "raw_action_text_uninterpreted"
    return "unknown"


def _message_for_event(event: Qihoo360ParsedEvent) -> str:
    threat = event.threat_name or "unknown threat"
    if _is_quarantine_path(event.quarantine_path):
        return f"Qihoo 360 quarantine summary recorded for {threat}"
    if event.threat_name:
        return f"Qihoo 360 detection summary recorded for {threat}"
    return "Qihoo 360 summary record observed"


def _confidence_for_attribution(attribution: str) -> str:
    return {"strong": "high", "medium": "medium", "weak": "low"}.get(attribution, "low")


def _is_quarantine_path(value: str) -> bool:
    normalized = value.replace("/", "\\").casefold()
    return normalized.startswith(QUARANTINE_PREFIX)


def _stat_metadata(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as exc:
        return {"error": type(exc).__name__}
    return {"size": stat.st_size, "mtime_utc": _format_mtime(stat.st_mtime)}


def _format_mtime(timestamp: float) -> str:
    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_error(exc: Exception) -> str:
    message = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return message[:300] or type(exc).__name__
