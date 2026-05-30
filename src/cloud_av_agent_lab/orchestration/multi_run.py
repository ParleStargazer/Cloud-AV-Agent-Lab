from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, TypeAlias

SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION = "multi-run-sample.v1"
BATCH_PLAN_SCHEMA_VERSION = "multi-run-plan.v1"
MULTI_RUN_STATE_SCHEMA_VERSION = "multi-run-state.v1"
MULTI_RUN_EVENT_SCHEMA_VERSION = "multi-run-event.v1"

FailureKind: TypeAlias = Literal[
    "planning_or_policy_failure",
    "case_failure",
    "environment_failure",
]
Verdict: TypeAlias = Literal[
    "detected_or_blocked",
    "allowed_executed",
    "not_delivered",
    "not_executed",
    "inconclusive",
    "not_evaluable",
    "unknown",
]
ReadinessStatus: TypeAlias = Literal["ok", "warning", "unknown", "skipped"]
CleanupStatus: TypeAlias = Literal[
    "restored",
    "restore_failed",
    "unknown",
    "not_started",
    "skipped",
]
EvidenceStatus: TypeAlias = Literal[
    "exported",
    "partial",
    "failed",
    "not_started",
    "skipped",
]
SummaryStatus: TypeAlias = Literal[
    "collected",
    "missing",
    "failed",
    "not_started",
    "skipped",
]
CaseStatus: TypeAlias = Literal[
    "planned",
    "completed",
    "failed",
    "skipped",
    "stopped_environment_failure",
]
SingleRunStatus: TypeAlias = Literal[
    "not_started",
    "completed",
    "failed",
    "timeout",
    "unknown",
]
BatchState: TypeAlias = Literal[
    "created",
    "planning",
    "manifest_ready",
    "preflight_passed",
    "running",
    "stopping",
    "stopped_by_user",
    "stopped_for_case_failure",
    "stopped_for_environment_failure",
    "stopped_for_manual_intervention",
    "completed",
    "completed_with_case_failures",
    "completed_with_warnings",
    "failed_invalid_config",
    "failed_manifest_mismatch",
]
SelectionMode: TypeAlias = Literal["all", "range", "indexes", "from_to"]
EntryStatus: TypeAlias = Literal["ready", "skipped", "invalid"]
SampleSourceKind: TypeAlias = Literal["local_platform_path", "external_reference"]

FAILURE_KINDS: tuple[str, ...] = (
    "planning_or_policy_failure",
    "case_failure",
    "environment_failure",
)
VERDICTS: tuple[str, ...] = (
    "detected_or_blocked",
    "allowed_executed",
    "not_delivered",
    "not_executed",
    "inconclusive",
    "not_evaluable",
    "unknown",
)
READINESS_STATUSES: tuple[str, ...] = ("ok", "warning", "unknown", "skipped")
CLEANUP_STATUSES: tuple[str, ...] = (
    "restored",
    "restore_failed",
    "unknown",
    "not_started",
    "skipped",
)
EVIDENCE_STATUSES: tuple[str, ...] = (
    "exported",
    "partial",
    "failed",
    "not_started",
    "skipped",
)
SUMMARY_STATUSES: tuple[str, ...] = (
    "collected",
    "missing",
    "failed",
    "not_started",
    "skipped",
)
BATCH_STATES: tuple[str, ...] = (
    "created",
    "planning",
    "manifest_ready",
    "preflight_passed",
    "running",
    "stopping",
    "stopped_by_user",
    "stopped_for_case_failure",
    "stopped_for_environment_failure",
    "stopped_for_manual_intervention",
    "completed",
    "completed_with_case_failures",
    "completed_with_warnings",
    "failed_invalid_config",
    "failed_manifest_mismatch",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SampleManifestEntry:
    sample_index: int
    sample_id: str
    case_name: str
    sha256: str
    md5: str
    size: int
    original_filename: str
    original_suffix: str
    normalized_suffix: str
    renamed_filename: str
    sample_ref: str
    manifest_id: str = ""
    manifest_created_at_utc: str = ""
    manifest_tool_version: str = ""
    sample_source_kind: SampleSourceKind = "local_platform_path"
    duplicate_group_id: str = ""
    duplicate_of_sample_index: int | None = None
    aliases: tuple[str, ...] = ()
    entry_status: EntryStatus = "ready"
    skip_reason: str | None = None
    created_at_utc: str = ""
    schema_version: str = SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "manifest_created_at_utc": self.manifest_created_at_utc,
            "manifest_tool_version": self.manifest_tool_version,
            "sample_index": self.sample_index,
            "sample_id": self.sample_id,
            "case_name": self.case_name,
            "sha256": self.sha256,
            "md5": self.md5,
            "size": self.size,
            "original_filename": self.original_filename,
            "original_suffix": self.original_suffix,
            "normalized_suffix": self.normalized_suffix,
            "renamed_filename": self.renamed_filename,
            "sample_source_kind": self.sample_source_kind,
            "sample_ref": self.sample_ref,
            "duplicate_group_id": self.duplicate_group_id,
            "duplicate_of_sample_index": self.duplicate_of_sample_index,
            "aliases": list(self.aliases),
            "entry_status": self.entry_status,
            "skip_reason": self.skip_reason,
            "created_at_utc": self.created_at_utc,
        }


@dataclass(frozen=True)
class BatchSelection:
    mode: SelectionMode
    selected_indexes: tuple[int, ...] = ()
    range_text: str = ""
    from_index: int | None = None
    to_index: int | None = None
    max_cases: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "selected_indexes": list(self.selected_indexes),
            "range": self.range_text,
            "from_index": self.from_index,
            "to_index": self.to_index,
            "max_cases": self.max_cases,
        }


@dataclass(frozen=True)
class BatchExecutionPolicy:
    mode: str = "serial"
    failure_policy: str = "continue"
    dry_run: bool = False
    case_timeout_seconds: float | None = None
    environment_failure_policy: str = "stop"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "failure_policy": self.failure_policy,
            "dry_run": self.dry_run,
            "case_timeout_seconds": self.case_timeout_seconds,
            "environment_failure_policy": self.environment_failure_policy,
        }


@dataclass(frozen=True)
class BatchPlan:
    batch_id: str
    created_at_utc: str
    product_id: str
    instance_id: str
    snapshot_id: str
    region: str
    sample_manifest_path: str
    manifest_sha256: str
    generated_config_sha256: str
    selection: BatchSelection
    execution: BatchExecutionPolicy = field(default_factory=BatchExecutionPolicy)
    single_run_runner_version: str = ""
    multi_run_version: str = ""
    product_profile_version: str = ""
    schema_version: str = BATCH_PLAN_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "created_at_utc": self.created_at_utc,
            "product_id": self.product_id,
            "instance_id": self.instance_id,
            "snapshot_id": self.snapshot_id,
            "region": self.region,
            "sample_manifest_path": self.sample_manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "generated_config_sha256": self.generated_config_sha256,
            "single_run_runner_version": self.single_run_runner_version,
            "multi_run_version": self.multi_run_version,
            "product_profile_version": self.product_profile_version,
            "selection": self.selection.to_dict(),
            "execution": self.execution.to_dict(),
        }


@dataclass(frozen=True)
class CaseState:
    sample_index: int
    sample_id: str
    case_id: str
    run_id: str = ""
    attempt: int = 0
    case_status: CaseStatus = "planned"
    single_run_status: SingleRunStatus = "not_started"
    cleanup_status: CleanupStatus = "not_started"
    evidence_status: EvidenceStatus = "not_started"
    summary_status: SummaryStatus = "not_started"
    readiness_status: ReadinessStatus = "unknown"
    resume_eligible: bool = False
    verdict: Verdict = "unknown"
    confidence: str = ""
    failure_kind: FailureKind | None = None
    error_summary: str = ""
    evidence_bundle_path: str = ""
    run_state_path: str = ""
    case_summary_path: str = ""
    duration_seconds: float | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_index": self.sample_index,
            "sample_id": self.sample_id,
            "case_id": self.case_id,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "case_status": self.case_status,
            "single_run_status": self.single_run_status,
            "cleanup_status": self.cleanup_status,
            "evidence_status": self.evidence_status,
            "summary_status": self.summary_status,
            "readiness_status": self.readiness_status,
            "resume_eligible": self.resume_eligible,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "failure_kind": self.failure_kind,
            "error_summary": self.error_summary,
            "evidence_bundle_path": self.evidence_bundle_path,
            "run_state_path": self.run_state_path,
            "case_summary_path": self.case_summary_path,
            "duration_seconds": self.duration_seconds,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class MultiRunState:
    batch_id: str
    batch_state: BatchState
    product_id: str
    instance_id: str
    snapshot_id: str
    region: str
    sample_manifest_path: str
    manifest_sha256: str
    batch_plan_sha256: str
    selected_indexes: tuple[int, ...]
    cases: tuple[CaseState, ...] = ()
    unsafe_to_continue: bool = False
    manual_intervention_required: bool = False
    manual_intervention_reason: str = ""
    started_at_utc: str = ""
    finished_at_utc: str = ""
    final_status: str = ""
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_version: str = MULTI_RUN_STATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "batch_state": self.batch_state,
            "product_id": self.product_id,
            "instance_id": self.instance_id,
            "snapshot_id": self.snapshot_id,
            "region": self.region,
            "sample_manifest_path": self.sample_manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "batch_plan_sha256": self.batch_plan_sha256,
            "selected_indexes": list(self.selected_indexes),
            "unsafe_to_continue": self.unsafe_to_continue,
            "manual_intervention_required": self.manual_intervention_required,
            "manual_intervention_reason": self.manual_intervention_reason,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "final_status": self.final_status,
            "cases": [case.to_dict() for case in self.cases],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class MultiRunEvent:
    seq: int
    event_type: str
    at_utc: str
    batch_id: str
    sample_index: int | None = None
    sample_id: str = ""
    run_id: str = ""
    case_id: str = ""
    case_status: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    schema_version: str = MULTI_RUN_EVENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "seq": self.seq,
            "type": self.event_type,
            "at_utc": self.at_utc,
            "batch_id": self.batch_id,
            "sample_index": self.sample_index,
            "sample_id": self.sample_id,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "case_status": self.case_status,
            "data": _jsonable(self.data),
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
