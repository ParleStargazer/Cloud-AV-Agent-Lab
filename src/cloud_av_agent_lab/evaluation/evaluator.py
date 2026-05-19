from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .models import EvaluationSummary

TERMINAL_EXECUTION_OBSERVED_STATES = {
    "exited_cleanly",
    "exited_with_error",
    "running",
    "timeout_still_running",
}
UNOBSERVED_EXECUTION_STATES = {
    "",
    "not_started",
    "execution_disabled",
    "execution_dry_run_checked",
    "unknown",
}
NONFATAL_EXECUTION_FAILURE_STATES = {
    "desktop_worker_not_ready",
    "lease_invalid",
    "lease_expired",
    "lease_reused",
    "worker_busy",
    "sample_missing_before_execution",
    "sha256_mismatch",
    "unsupported_file_type",
    "launch_failed",
    "not_uploaded",
}
COLLECTION_UNAVAILABLE_STATES = {"", "failed", "not_collected", "unknown"}
COLLECTION_PARTIAL_STATES = {"partial"}


def evaluate_case(
    case_report: Mapping[str, Any],
    case_collection: Mapping[str, Any] | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
    max_timeline_events: int = 20,
) -> EvaluationSummary:
    collection_payload = case_collection or {}
    execution = _mapping(case_report.get("execution"))
    collection = _collection_summary(case_report, collection_payload)
    delivery = _delivery_summary(case_report)
    timeline = _simple_timeline(collection_payload, events or (), max_timeline_events)
    verdict, confidence, summary, reasons, blocking, nonfatal = _decide_verdict(
        delivery=delivery,
        execution=execution,
        collection=collection,
    )
    return EvaluationSummary(
        case_id=str(case_report.get("case_id", "")),
        sample_id=str(case_report.get("sample_id", "")),
        vm_id=str(case_report.get("vm_id", "")),
        product_id=str(
            case_report.get("product_id") or collection.get("product_id") or ""
        ),
        verdict=verdict,
        confidence=confidence,
        summary=summary,
        reasons=tuple(reasons),
        delivery=delivery,
        execution=execution,
        collection=collection,
        decision_inputs={
            "delivery": delivery,
            "execution": execution,
            "collection": collection,
            "environment": {},
        },
        blocking_conditions=tuple(blocking),
        nonfatal_failures=tuple(nonfatal),
        timeline=tuple(timeline),
        generated_at_utc=_utc_now(),
    )


def render_summary_markdown(summary: EvaluationSummary | Mapping[str, Any]) -> str:
    payload = summary.to_dict() if isinstance(summary, EvaluationSummary) else summary
    lines = [
        f"# Case Summary: {payload.get('case_id', '')}",
        "",
        f"- Product: {payload.get('product_id', '')}",
        f"- Sample: {payload.get('sample_id', '')}",
        f"- Verdict: {payload.get('verdict', '')}",
        f"- Confidence: {payload.get('confidence', '')}",
        "",
        "## Summary",
        "",
        str(payload.get("summary", "")),
        "",
        "## Key Reasons",
        "",
    ]
    reasons = payload.get("reasons", [])
    if isinstance(reasons, Sequence) and not isinstance(reasons, (str, bytes)):
        lines.extend(f"- {reason}" for reason in reasons)
    else:
        lines.append("- no structured reasons recorded")
    lines.extend(["", "## Timeline", ""])
    timeline = payload.get("timeline", [])
    if isinstance(timeline, Sequence) and not isinstance(timeline, (str, bytes)):
        for item in timeline:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                "- "
                f"{item.get('timestamp_utc', '')} "
                f"[{item.get('source', '')}] "
                f"{item.get('event_type', '')}: {item.get('message', '')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _decide_verdict(
    delivery: Mapping[str, Any],
    execution: Mapping[str, Any],
    collection: Mapping[str, Any],
) -> tuple[str, str, str, list[str], list[str], list[str]]:
    reasons: list[str] = []
    blocking: list[str] = []
    nonfatal: list[str] = []
    evidence_count = _coerce_int(collection.get("evidence_count")) or 0
    collection_intercepted = collection.get("intercepted") is True
    collection_verdict = str(collection.get("verdict", "")).casefold()
    if collection_intercepted or (
        evidence_count > 0 and collection_verdict in {"intercepted", "detected"}
    ):
        reasons.append("collection evidence_count>0")
        reasons.append("product log evidence indicates detection or blocking")
        return (
            "detected_or_blocked",
            "high",
            "安全产品日志中存在匹配当前 case 的检出或拦截证据。",
            reasons,
            blocking,
            nonfatal,
        )

    collection_state = str(collection.get("state", "")).casefold()
    execution_state = str(execution.get("state", "")).casefold()
    collection_unavailable = _collection_unavailable(collection)
    collection_partial = collection_state in COLLECTION_PARTIAL_STATES
    collection_errors = _list_value(collection.get("errors"))

    if execution_state == "terminated_or_disappeared":
        reasons.append("execution_state=terminated_or_disappeared")
        reasons.append("process disappearance is not treated as AV detection alone")
        nonfatal.append("terminated_or_disappeared_without_product_evidence")
        return (
            "inconclusive",
            "low",
            "进程已退出或不可观察；该现象本身不能单独证明安全产品拦截。",
            reasons,
            blocking,
            nonfatal,
        )

    if bool(delivery.get("removed_after_save")):
        reasons.append("upload_state=removed_after_save")
        if collection_unavailable:
            reasons.append("removed_after_save but collection evidence unavailable")
            blocking.append(f"collection_state={collection_state or 'unknown'}")
            return (
                "inconclusive",
                "low",
                "样本曾保存成功但随后消失，但产品日志证据不可用，结论保持保守。",
                reasons,
                blocking,
                nonfatal,
            )
        reasons.append("no matching product-log evidence was collected")
        return (
            "suspiciously_removed",
            "medium",
            "样本曾保存成功但随后消失；缺少产品日志证据，按可疑移除保守记录。",
            reasons,
            blocking,
            nonfatal,
        )

    if bool(delivery.get("stable")) and evidence_count == 0:
        reasons.append("upload_state=stable")
        reasons.append("collection evidence_count=0")
        if execution_state in UNOBSERVED_EXECUTION_STATES:
            reasons.append("execution was not observed")
            blocking.append(f"execution_state={execution_state or 'unknown'}")
            return (
                "execution_not_observed",
                "low",
                "样本稳定存在，但执行阶段未发生或不可观察，不能武断判定未检出。",
                reasons,
                blocking,
                nonfatal,
            )
        if execution_state in NONFATAL_EXECUTION_FAILURE_STATES:
            reasons.append(f"execution_state={execution_state}")
            nonfatal.append(execution_state)
            return (
                "inconclusive",
                "low",
                "执行阶段未能形成可靠观察，结论保持保守。",
                reasons,
                blocking,
                nonfatal,
            )
        if collection_unavailable:
            reasons.append(f"collection_state={collection_state or 'unknown'}")
            blocking.append(f"collection_state={collection_state or 'unknown'}")
            return (
                "inconclusive",
                "low",
                "样本稳定存在，但收集阶段不可用，不能判定未检出。",
                reasons,
                blocking,
                nonfatal,
            )
        if collection_partial or collection_errors:
            reasons.append(f"collection_state={collection_state or 'partial'}")
            if collection_errors:
                reasons.append("collection has errors")
            blocking.append("collection_partial_or_errors")
            return (
                "inconclusive",
                "low",
                "收集阶段不完整或存在错误，未发现证据不能等同于未检出。",
                reasons,
                blocking,
                nonfatal,
            )
        if (
            execution_state in TERMINAL_EXECUTION_OBSERVED_STATES
            and collection_state == "collected"
            and collection_verdict == "not_intercepted"
            and _collection_window_covers_execution(collection, execution)
        ):
            return (
                "no_detection_observed",
                "medium",
                "当前观察窗口内未发现匹配的安全产品拦截证据。",
                reasons,
                blocking,
                nonfatal,
            )
        reasons.append(f"execution_state={execution_state or 'unknown'}")
        reasons.append("collection window or verdict is insufficient for no_detection")
        blocking.append("no_detection_preconditions_not_met")
        return (
            "inconclusive",
            "low",
            "投送和收集信息不足，结论保持保守。",
            reasons,
            blocking,
            nonfatal,
        )

    reasons.append("insufficient delivery, execution, or collection evidence")
    return (
        "inconclusive",
        "low",
        "现有证据不足，保持保守结论。",
        reasons,
        blocking,
        nonfatal,
    )


def _delivery_summary(case_report: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "upload_state",
        "saved_once",
        "post_write_exists",
        "removed_after_save",
        "locked_or_busy",
        "stable",
        "original_filename",
        "sha256",
        "size",
        "prepared_at_utc",
        "uploaded_at_utc",
        "updated_at_utc",
    )
    return {key: case_report.get(key) for key in keys}


def _collection_summary(
    case_report: Mapping[str, Any],
    case_collection: Mapping[str, Any],
) -> dict[str, Any]:
    report_collection = _mapping(case_report.get("collection"))
    return {
        "product_id": str(
            case_collection.get("product_id") or report_collection.get("product_id", "")
        ),
        "state": str(
            case_collection.get("collection_state")
            or report_collection.get("state")
            or "not_collected"
        ),
        "verdict": str(
            case_collection.get("verdict")
            or report_collection.get("verdict")
            or "unknown"
        ),
        "intercepted": (
            case_collection.get("intercepted")
            if "intercepted" in case_collection
            else report_collection.get("intercepted")
        ),
        "reason": str(
            case_collection.get("reason") or report_collection.get("reason", "")
        ),
        "evidence_count": _coerce_int(
            case_collection.get("evidence_count")
            if "evidence_count" in case_collection
            else report_collection.get("evidence_count")
        )
        or 0,
        "collected_at_utc": str(
            case_collection.get("collected_at_utc")
            or report_collection.get("collected_at_utc", "")
        ),
        "errors": _list_value(case_collection.get("errors"))
        or _list_value(report_collection.get("errors")),
        "window": _mapping(case_collection.get("window"))
        or _mapping(report_collection.get("window")),
    }


def _simple_timeline(
    case_collection: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    max_timeline_events: int,
) -> list[dict[str, Any]]:
    timeline = _list_of_mappings(case_collection.get("timeline"))
    if not timeline:
        timeline = [
            {
                "timestamp_utc": str(event.get("timestamp_utc", "")),
                "source": "guest_agent",
                "event_type": str(event.get("event_type", "")),
                "message": str(event.get("message", "")),
            }
            for event in events
        ]
    compact_timeline = _compact_timeline(timeline)
    return compact_timeline[-max(0, max_timeline_events) :]


def _compact_timeline(timeline: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    last_sample_state = ""
    last_execution_signature: tuple[str, int | None, int | None] | None = None
    for item in timeline:
        event = dict(item)
        event_type = str(event.get("event_type", ""))
        if event_type == "sample_post_upload_check":
            continue
        if event_type in {
            "sample_stable_after_upload",
            "sample_locked_or_busy",
            "sample_removed_after_save",
        }:
            sample_state = _sample_state_from_event(event_type, event)
            if sample_state == last_sample_state:
                continue
            last_sample_state = sample_state
        elif event_type == "execution_observed":
            signature = _execution_signature(event)
            if signature == last_execution_signature:
                continue
            last_execution_signature = signature
        elif event_type == "execution_child_observed":
            continue
        compact.append(event)
    return compact


def _sample_state_from_event(
    event_type: str,
    event: Mapping[str, Any],
) -> str:
    evidence = _mapping(event.get("evidence"))
    upload_state = str(evidence.get("upload_state") or "").strip()
    if upload_state:
        return upload_state
    return {
        "sample_stable_after_upload": "stable",
        "sample_locked_or_busy": "locked_or_busy",
        "sample_removed_after_save": "removed_after_save",
    }.get(event_type, event_type)


def _execution_signature(
    event: Mapping[str, Any],
) -> tuple[str, int | None, int | None]:
    evidence = _mapping(event.get("evidence"))
    return (
        str(evidence.get("execution_state") or ""),
        _coerce_int(evidence.get("children_count")),
        _coerce_int(evidence.get("exit_code")),
    )


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_value(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _list_of_mappings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _collection_unavailable(collection: Mapping[str, Any]) -> bool:
    state = str(collection.get("state", "")).casefold()
    return state in COLLECTION_UNAVAILABLE_STATES


def _collection_window_covers_execution(
    collection: Mapping[str, Any],
    execution: Mapping[str, Any],
) -> bool:
    window = _mapping(collection.get("window"))
    execution_started = str(
        window.get("execution_started_at_utc") or execution.get("started_at_utc") or ""
    )
    if not execution_started:
        return False
    window_end = str(
        window.get("end_utc") or window.get("collection_finished_at_utc") or ""
    )
    if not window_end:
        return False
    started = _parse_utc(execution_started)
    ended = _parse_utc(window_end)
    return bool(started and ended and ended >= started)


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
