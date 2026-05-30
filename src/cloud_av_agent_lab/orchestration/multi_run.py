from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias

SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION = "multi-run-sample.v1"
BATCH_PLAN_SCHEMA_VERSION = "multi-run-plan.v1"
MULTI_RUN_STATE_SCHEMA_VERSION = "multi-run-state.v1"
MULTI_RUN_EVENT_SCHEMA_VERSION = "multi-run-event.v1"
MULTI_RUN_VERSION = "multi-run.v1"

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
FakeSingleRunScenario: TypeAlias = Literal[
    "completed",
    "case_failed",
    "environment_failed",
    "timeout",
    "summary_missing",
    "cleanup_unknown",
    "cleanup_restore_failed",
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

ALLOWED_SAMPLE_SOURCE_KINDS: tuple[str, ...] = (
    "local_platform_path",
    "external_reference",
)
ALLOWED_ENTRY_STATUSES: tuple[str, ...] = ("ready", "skipped", "invalid")

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


class MultiRunManifestError(ValueError):
    """Raised when a multi-run sample manifest is malformed."""


class MultiRunSelectionError(ValueError):
    """Raised when multi-run sample selection cannot produce a safe plan."""

    failure_kind: FailureKind = "planning_or_policy_failure"


class MultiRunPlanError(ValueError):
    """Raised when immutable multi-run batch planning fails."""

    failure_kind: FailureKind = "planning_or_policy_failure"


class MultiRunStateError(ValueError):
    """Raised when multi-run state or event artifacts cannot be loaded safely."""

    failure_kind: FailureKind = "planning_or_policy_failure"


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
class LoadedSampleManifest:
    path: Path
    sha256: str
    entries: tuple[SampleManifestEntry, ...]

    @property
    def indexes(self) -> tuple[int, ...]:
        return tuple(entry.sample_index for entry in self.entries)

    def by_index(self) -> dict[int, SampleManifestEntry]:
        return {entry.sample_index: entry for entry in self.entries}


def compute_manifest_sha256(path: Path | str) -> str:
    manifest_path = Path(path)
    digest = hashlib.sha256()
    with manifest_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def compute_text_sha256(value: str) -> str:
    return compute_bytes_sha256(value.encode("utf-8"))


def load_sample_manifest(path: Path | str) -> LoadedSampleManifest:
    manifest_path = Path(path)
    entries: list[SampleManifestEntry] = []
    seen_indexes: set[int] = set()
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                payload = _load_manifest_line(line, line_number)
                entry = _parse_manifest_entry(payload, line_number)
                if entry.sample_index in seen_indexes:
                    raise MultiRunManifestError(
                        f"line {line_number}: duplicate sample_index "
                        f"{entry.sample_index}"
                    )
                seen_indexes.add(entry.sample_index)
                entries.append(entry)

        if not entries:
            raise MultiRunManifestError("sample manifest is empty")
    except MultiRunManifestError as exc:
        raise MultiRunManifestError(f"{manifest_path}: {exc}") from exc

    return LoadedSampleManifest(
        path=manifest_path,
        sha256=compute_manifest_sha256(manifest_path),
        entries=tuple(entries),
    )


def _load_manifest_line(line: str, line_number: int) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MultiRunManifestError(
            f"line {line_number}: invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise MultiRunManifestError(
            f"line {line_number}: manifest entry must be object"
        )
    return payload


def _parse_manifest_entry(
    payload: dict[str, Any],
    line_number: int,
) -> SampleManifestEntry:
    schema_version = _required_str(payload, "schema_version", line_number)
    if schema_version != SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION:
        raise MultiRunManifestError(
            f"line {line_number}: unsupported schema_version {schema_version!r}"
        )

    sample_index = _required_int(payload, "sample_index", line_number)
    if sample_index < 1:
        raise MultiRunManifestError(
            f"line {line_number}: sample_index must be a positive 1-based integer"
        )

    sample_source_kind = _optional_str(
        payload,
        "sample_source_kind",
        line_number,
        default="local_platform_path",
    )
    if sample_source_kind not in ALLOWED_SAMPLE_SOURCE_KINDS:
        raise MultiRunManifestError(
            f"line {line_number}: invalid sample_source_kind {sample_source_kind!r}"
        )

    entry_status = _optional_str(
        payload,
        "entry_status",
        line_number,
        default="ready",
    )
    if entry_status not in ALLOWED_ENTRY_STATUSES:
        raise MultiRunManifestError(
            f"line {line_number}: invalid entry_status {entry_status!r}"
        )

    sha256 = _required_hex(payload, "sha256", line_number, expected_length=64)
    md5 = _required_hex(payload, "md5", line_number, expected_length=32)
    size = _required_int(payload, "size", line_number)
    if size < 0:
        raise MultiRunManifestError(f"line {line_number}: size must be >= 0")

    return SampleManifestEntry(
        schema_version=schema_version,
        manifest_id=_optional_str(payload, "manifest_id", line_number),
        manifest_created_at_utc=_optional_str(
            payload,
            "manifest_created_at_utc",
            line_number,
        ),
        manifest_tool_version=_optional_str(
            payload,
            "manifest_tool_version",
            line_number,
        ),
        sample_index=sample_index,
        sample_id=_required_str(payload, "sample_id", line_number),
        case_name=_required_str(payload, "case_name", line_number),
        sha256=sha256,
        md5=md5,
        size=size,
        original_filename=_required_str(payload, "original_filename", line_number),
        original_suffix=_required_str(payload, "original_suffix", line_number),
        normalized_suffix=_required_str(payload, "normalized_suffix", line_number),
        renamed_filename=_required_str(payload, "renamed_filename", line_number),
        sample_source_kind=sample_source_kind,  # type: ignore[arg-type]
        sample_ref=_required_str(payload, "sample_ref", line_number),
        duplicate_group_id=_optional_str(payload, "duplicate_group_id", line_number),
        duplicate_of_sample_index=_optional_int_or_none(
            payload,
            "duplicate_of_sample_index",
            line_number,
        ),
        aliases=tuple(_optional_str_list(payload, "aliases", line_number)),
        entry_status=entry_status,  # type: ignore[arg-type]
        skip_reason=_optional_str_or_none(payload, "skip_reason", line_number),
        created_at_utc=_optional_str(payload, "created_at_utc", line_number),
    )


def _required_str(
    payload: dict[str, Any],
    key: str,
    line_number: int,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise MultiRunManifestError(f"line {line_number}: missing or invalid {key}")
    return value


def _optional_str(
    payload: dict[str, Any],
    key: str,
    line_number: int,
    *,
    default: str = "",
) -> str:
    value = payload.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise MultiRunManifestError(f"line {line_number}: invalid {key}")
    return value


def _optional_str_or_none(
    payload: dict[str, Any],
    key: str,
    line_number: int,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MultiRunManifestError(f"line {line_number}: invalid {key}")
    return value


def _required_int(
    payload: dict[str, Any],
    key: str,
    line_number: int,
) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MultiRunManifestError(f"line {line_number}: missing or invalid {key}")
    return value


def _optional_int_or_none(
    payload: dict[str, Any],
    key: str,
    line_number: int,
) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise MultiRunManifestError(f"line {line_number}: invalid {key}")
    return value


def _required_hex(
    payload: dict[str, Any],
    key: str,
    line_number: int,
    *,
    expected_length: int,
) -> str:
    value = _required_str(payload, key, line_number).casefold()
    if len(value) != expected_length or any(
        ch not in "0123456789abcdef" for ch in value
    ):
        raise MultiRunManifestError(
            f"line {line_number}: {key} must be {expected_length} hex characters"
        )
    return value


def _optional_str_list(
    payload: dict[str, Any],
    key: str,
    line_number: int,
) -> list[str]:
    value = payload.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise MultiRunManifestError(f"line {line_number}: invalid {key}")
    return value


def parse_sample_selection(
    available_indexes: Iterable[int],
    *,
    all_samples: bool = False,
    range_text: str = "",
    indexes_text: str = "",
    from_index: int | None = None,
    to_index: int | None = None,
    max_cases: int | None = None,
) -> BatchSelection:
    available = _normalize_available_indexes(available_indexes)
    if not available:
        raise MultiRunSelectionError("manifest has no selectable sample indexes")
    if max_cases is not None and max_cases < 1:
        raise MultiRunSelectionError("--max-cases must be a positive integer")

    has_from_to = from_index is not None or to_index is not None
    modes = [
        name
        for name, enabled in (
            ("all", all_samples),
            ("range", bool(range_text)),
            ("indexes", bool(indexes_text)),
            ("from_to", has_from_to),
        )
        if enabled
    ]
    if len(modes) != 1:
        raise MultiRunSelectionError(
            "exactly one selection mode is required: --all, --range, "
            "--indexes, or --from/--to"
        )

    mode = modes[0]
    if mode == "all":
        selected = available
        selection = BatchSelection(
            mode="all",
            selected_indexes=_apply_max_cases(selected, max_cases),
            max_cases=max_cases,
        )
    elif mode == "range":
        start, end = _parse_closed_range(range_text, "--range")
        selected = tuple(range(start, end + 1))
        _ensure_selected_indexes_available(selected, available)
        selection = BatchSelection(
            mode="range",
            selected_indexes=_apply_max_cases(selected, max_cases),
            range_text=f"{start}-{end}",
            max_cases=max_cases,
        )
    elif mode == "indexes":
        selected = _parse_indexes_text(indexes_text)
        _ensure_selected_indexes_available(selected, available)
        selection = BatchSelection(
            mode="indexes",
            selected_indexes=_apply_max_cases(selected, max_cases),
            max_cases=max_cases,
        )
    else:
        if from_index is None or to_index is None:
            raise MultiRunSelectionError("--from and --to must be provided together")
        start = _positive_index(from_index, "--from")
        end = _positive_index(to_index, "--to")
        if start > end:
            raise MultiRunSelectionError("--from must be <= --to")
        selected = tuple(range(start, end + 1))
        _ensure_selected_indexes_available(selected, available)
        selection = BatchSelection(
            mode="from_to",
            selected_indexes=_apply_max_cases(selected, max_cases),
            from_index=start,
            to_index=end,
            max_cases=max_cases,
        )

    if not selection.selected_indexes:
        raise MultiRunSelectionError("selection produced no sample indexes")
    return selection


def _normalize_available_indexes(available_indexes: Iterable[int]) -> tuple[int, ...]:
    indexes: set[int] = set()
    for value in available_indexes:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise MultiRunSelectionError(
                "available sample indexes must be positive integers"
            )
        indexes.add(value)
    return tuple(sorted(indexes))


def _apply_max_cases(
    selected_indexes: tuple[int, ...],
    max_cases: int | None,
) -> tuple[int, ...]:
    if max_cases is None:
        return selected_indexes
    return selected_indexes[:max_cases]


def _parse_closed_range(value: str, option_name: str) -> tuple[int, int]:
    parts = [part.strip() for part in value.split("-", maxsplit=1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise MultiRunSelectionError(f"{option_name} must use START-END syntax")
    try:
        start = int(parts[0])
        end = int(parts[1])
    except ValueError as exc:
        raise MultiRunSelectionError(
            f"{option_name} must contain integer indexes"
        ) from exc
    start = _positive_index(start, f"{option_name} start")
    end = _positive_index(end, f"{option_name} end")
    if start > end:
        raise MultiRunSelectionError(f"{option_name} start must be <= end")
    return start, end


def _parse_indexes_text(value: str) -> tuple[int, ...]:
    raw_parts = [part.strip() for part in value.split(",")]
    if not raw_parts or any(not part for part in raw_parts):
        raise MultiRunSelectionError("--indexes must be a comma-separated list")
    indexes: set[int] = set()
    for part in raw_parts:
        try:
            parsed = int(part)
        except ValueError as exc:
            raise MultiRunSelectionError(
                "--indexes must contain integer indexes"
            ) from exc
        indexes.add(_positive_index(parsed, "--indexes"))
    return tuple(sorted(indexes))


def _positive_index(value: int, label: str) -> int:
    if value < 1:
        raise MultiRunSelectionError(f"{label} must be a positive 1-based index")
    return value


def _ensure_selected_indexes_available(
    selected_indexes: tuple[int, ...],
    available_indexes: tuple[int, ...],
) -> None:
    available = set(available_indexes)
    missing = [value for value in selected_indexes if value not in available]
    if missing:
        formatted = ", ".join(str(value) for value in missing[:5])
        suffix = "" if len(missing) <= 5 else ", ..."
        raise MultiRunSelectionError(
            f"selection references unavailable sample_index values: {formatted}{suffix}"
        )


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
    plan_only: bool = False
    case_timeout_seconds: float | None = None
    environment_failure_policy: str = "stop"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "failure_policy": self.failure_policy,
            "dry_run": self.dry_run,
            "plan_only": self.plan_only,
            "case_timeout_seconds": self.case_timeout_seconds,
            "environment_failure_policy": self.environment_failure_policy,
        }


@dataclass(frozen=True)
class MultiRunPlanArtifacts:
    batch_dir: Path
    batch_plan_path: Path
    generated_config_path: Path
    manifest_copy_path: Path
    manifest_sha256_path: Path
    state_path: Path
    event_log_path: Path
    batch_plan_sha256: str
    batch_plan: BatchPlan

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_dir": str(self.batch_dir),
            "batch_plan_path": str(self.batch_plan_path),
            "generated_config_path": str(self.generated_config_path),
            "manifest_copy_path": str(self.manifest_copy_path),
            "manifest_sha256_path": str(self.manifest_sha256_path),
            "state_path": str(self.state_path),
            "event_log_path": str(self.event_log_path),
            "batch_plan_sha256": self.batch_plan_sha256,
            "batch_plan": self.batch_plan.to_dict(),
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


def create_multi_run_batch_plan(
    *,
    batch_root: Path | str,
    batch_id: str,
    product_id: str,
    instance_id: str,
    snapshot_id: str,
    region: str,
    guest_agent_url: str,
    desktop_worker_url: str,
    manifest: LoadedSampleManifest,
    selection: BatchSelection,
    dry_run: bool,
    failure_policy: str,
    plan_only: bool = False,
) -> MultiRunPlanArtifacts:
    resolved_batch_id = _safe_batch_id(batch_id or default_batch_id(product_id))
    batch_dir = Path(batch_root) / resolved_batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    batch_plan_path = batch_dir / "batch_plan.json"
    generated_config_path = batch_dir / "multi_run.generated.toml"
    manifest_copy_path = batch_dir / "sample_manifest.jsonl"
    manifest_sha256_path = batch_dir / "sample_manifest.sha256"
    state_path = batch_dir / "multi_run_state.json"
    event_log_path = batch_dir / "multi_run_events.jsonl"
    _ensure_plan_artifacts_do_not_exist(
        batch_plan_path,
        generated_config_path,
        manifest_copy_path,
        manifest_sha256_path,
        state_path,
        event_log_path,
    )
    _copy_manifest_bytes(manifest.path, manifest_copy_path)

    generated_config = render_multi_run_generated_config(
        product_id=product_id,
        instance_id=instance_id,
        snapshot_id=snapshot_id,
        region=region,
        guest_agent_url=guest_agent_url,
        desktop_worker_url=desktop_worker_url,
        sample_manifest_path=manifest_copy_path.name,
        batch_plan_path=batch_plan_path.name,
        dry_run=dry_run,
        plan_only=plan_only,
    )
    generated_config_bytes = generated_config.encode("utf-8")
    generated_config_sha256 = compute_bytes_sha256(generated_config_bytes)
    plan = BatchPlan(
        batch_id=resolved_batch_id,
        created_at_utc=utc_now(),
        product_id=product_id,
        instance_id=instance_id,
        snapshot_id=snapshot_id,
        region=region,
        sample_manifest_path=manifest_copy_path.name,
        manifest_sha256=manifest.sha256,
        generated_config_sha256=generated_config_sha256,
        single_run_runner_version="single-run.v1",
        multi_run_version=MULTI_RUN_VERSION,
        product_profile_version=f"{product_id}.v1",
        selection=selection,
        execution=BatchExecutionPolicy(
            mode="serial",
            failure_policy=failure_policy,
            dry_run=dry_run,
            plan_only=plan_only,
        ),
    )

    batch_plan_bytes = (
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    batch_plan_path.write_bytes(batch_plan_bytes)
    batch_plan_sha256 = compute_bytes_sha256(batch_plan_bytes)
    generated_config_path.write_bytes(generated_config_bytes)
    manifest_sha256_path.write_text(
        f"{manifest.sha256}  {manifest_copy_path.name}\n",
        encoding="utf-8",
    )
    state = _initial_multi_run_state(
        plan,
        manifest=manifest,
        batch_plan_sha256=batch_plan_sha256,
    )
    write_multi_run_state(state_path, state)
    append_next_multi_run_event(
        event_log_path,
        batch_id=plan.batch_id,
        event_type="batch_created",
        data={
            "batch_plan_path": batch_plan_path.name,
            "state_path": state_path.name,
            "event_log_path": event_log_path.name,
        },
    )
    append_next_multi_run_event(
        event_log_path,
        batch_id=plan.batch_id,
        event_type="plan_created",
        data={
            "manifest_sha256": manifest.sha256,
            "generated_config_sha256": generated_config_sha256,
            "selected_indexes": selection.selected_indexes,
            "execution_mode": plan.execution.mode,
            "dry_run": dry_run,
            "plan_only": plan_only,
        },
    )

    return MultiRunPlanArtifacts(
        batch_dir=batch_dir,
        batch_plan_path=batch_plan_path,
        generated_config_path=generated_config_path,
        manifest_copy_path=manifest_copy_path,
        manifest_sha256_path=manifest_sha256_path,
        state_path=state_path,
        event_log_path=event_log_path,
        batch_plan_sha256=batch_plan_sha256,
        batch_plan=plan,
    )


def default_batch_id(product_id: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"batch_{stamp}_{_safe_batch_id(product_id or 'product')}"


def render_multi_run_generated_config(
    *,
    product_id: str,
    instance_id: str,
    snapshot_id: str,
    region: str,
    guest_agent_url: str,
    desktop_worker_url: str,
    dry_run: bool,
    sample_manifest_path: str = "sample_manifest.jsonl",
    batch_plan_path: str = "batch_plan.json",
    plan_only: bool = False,
) -> str:
    return "\n".join(
        [
            "# Generated by cloud-av-agent-lab multi-run.",
            "# Non-sensitive batch metadata only. Do not add private values here.",
            "",
            "[multi_run]",
            f"product_id = {_toml_string(product_id)}",
            f"instance_id = {_toml_string(instance_id)}",
            f"snapshot_id = {_toml_string(snapshot_id)}",
            f"region = {_toml_string(region)}",
            f"guest_agent_url = {_toml_string(guest_agent_url)}",
            f"desktop_worker_url = {_toml_string(desktop_worker_url)}",
            f"sample_manifest_path = {_toml_string(sample_manifest_path)}",
            f"batch_plan_path = {_toml_string(batch_plan_path)}",
            f"dry_run = {_toml_bool(dry_run)}",
            f"plan_only = {_toml_bool(plan_only)}",
            "",
        ]
    )


def _safe_batch_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    cleaned = cleaned.strip(".-_")
    if not cleaned:
        raise MultiRunPlanError("batch_id cannot be empty")
    return cleaned


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _ensure_plan_artifacts_do_not_exist(*paths: Path) -> None:
    existing = [path.name for path in paths if path.exists()]
    if existing:
        formatted = ", ".join(sorted(existing))
        raise MultiRunPlanError(f"batch plan artifact already exists: {formatted}")


def _copy_manifest_bytes(source: Path, destination: Path) -> None:
    source_path = source.resolve()
    destination_path = destination.resolve()
    if source_path == destination_path:
        return
    destination.write_bytes(source.read_bytes())


@dataclass(frozen=True)
class CaseState:
    sample_index: int
    sample_id: str
    case_name: str
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
            "case_name": self.case_name,
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


@dataclass(frozen=True)
class SingleRunRequest:
    batch_id: str
    sample_index: int
    sample_id: str
    case_name: str
    case_id: str
    run_id: str
    product_id: str
    instance_id: str
    snapshot_id: str
    region: str
    sample_ref: str
    manifest_sha256: str
    batch_plan_sha256: str
    sha256: str
    md5: str
    size: int
    original_filename: str
    case_dir: Path
    dry_run: bool
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "sample_index": self.sample_index,
            "sample_id": self.sample_id,
            "case_name": self.case_name,
            "case_id": self.case_id,
            "run_id": self.run_id,
            "product_id": self.product_id,
            "instance_id": self.instance_id,
            "snapshot_id": self.snapshot_id,
            "region": self.region,
            "sample_ref": self.sample_ref,
            "manifest_sha256": self.manifest_sha256,
            "batch_plan_sha256": self.batch_plan_sha256,
            "sha256": self.sha256,
            "md5": self.md5,
            "size": self.size,
            "original_filename": self.original_filename,
            "case_dir": str(self.case_dir),
            "dry_run": self.dry_run,
            "attempt": self.attempt,
        }


@dataclass(frozen=True)
class SingleRunRunnerResult:
    run_id: str
    case_id: str
    final_status: str
    case_status: CaseStatus
    single_run_status: SingleRunStatus
    cleanup_status: CleanupStatus
    evidence_status: EvidenceStatus
    summary_status: SummaryStatus
    readiness_status: ReadinessStatus = "unknown"
    verdict: Verdict = "unknown"
    confidence: str = ""
    failure_kind: FailureKind | None = None
    error_summary: str = ""
    evidence_bundle_path: str = ""
    run_state_path: str = ""
    case_summary_path: str = ""
    duration_seconds: float | None = None
    warnings: tuple[str, ...] = ()
    unsafe_to_continue: bool = False
    manual_intervention_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "final_status": self.final_status,
            "case_status": self.case_status,
            "single_run_status": self.single_run_status,
            "cleanup_status": self.cleanup_status,
            "evidence_status": self.evidence_status,
            "summary_status": self.summary_status,
            "readiness_status": self.readiness_status,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "failure_kind": self.failure_kind,
            "error_summary": self.error_summary,
            "evidence_bundle_path": self.evidence_bundle_path,
            "run_state_path": self.run_state_path,
            "case_summary_path": self.case_summary_path,
            "duration_seconds": self.duration_seconds,
            "warnings": list(self.warnings),
            "unsafe_to_continue": self.unsafe_to_continue,
            "manual_intervention_required": self.manual_intervention_required,
        }

    def to_case_state(self, request: SingleRunRequest) -> CaseState:
        return CaseState(
            sample_index=request.sample_index,
            sample_id=request.sample_id,
            case_name=request.case_name,
            case_id=self.case_id,
            run_id=self.run_id,
            attempt=request.attempt,
            case_status=self.case_status,
            single_run_status=self.single_run_status,
            cleanup_status=self.cleanup_status,
            evidence_status=self.evidence_status,
            summary_status=self.summary_status,
            readiness_status=self.readiness_status,
            resume_eligible=(
                self.case_status == "completed" and self.cleanup_status == "restored"
            ),
            verdict=self.verdict,
            confidence=self.confidence,
            failure_kind=self.failure_kind,
            error_summary=self.error_summary,
            evidence_bundle_path=self.evidence_bundle_path,
            run_state_path=self.run_state_path,
            case_summary_path=self.case_summary_path,
            duration_seconds=self.duration_seconds,
            warnings=self.warnings,
        )


class SingleRunRunner(Protocol):
    def run(self, request: SingleRunRequest) -> SingleRunRunnerResult:
        """Run one already-planned case and return normalized multi-run metadata."""


class FakeSingleRunRunner:
    def __init__(
        self,
        *,
        default_scenario: FakeSingleRunScenario = "completed",
        scenarios_by_sample_index: Mapping[int, FakeSingleRunScenario] | None = None,
    ) -> None:
        self.default_scenario = default_scenario
        self.scenarios_by_sample_index = dict(scenarios_by_sample_index or {})
        self.requests: list[SingleRunRequest] = []

    def run(self, request: SingleRunRequest) -> SingleRunRunnerResult:
        self.requests.append(request)
        scenario = self.scenarios_by_sample_index.get(
            request.sample_index, self.default_scenario
        )
        return fake_single_run_result(scenario, request)


def fake_single_run_result(
    scenario: FakeSingleRunScenario,
    request: SingleRunRequest,
) -> SingleRunRunnerResult:
    run_root = request.case_dir / "single_run"
    run_state_path = (run_root / "run_state.json").as_posix()
    summary_path = (run_root / "case_summary.json").as_posix()
    evidence_path = (run_root / f"case_evidence_{request.case_id}.zip").as_posix()
    base = {
        "run_id": request.run_id,
        "case_id": request.case_id,
        "run_state_path": run_state_path,
        "case_summary_path": summary_path,
        "evidence_bundle_path": evidence_path,
        "duration_seconds": 1.0,
    }
    if scenario == "completed":
        return SingleRunRunnerResult(
            **base,
            final_status="completed",
            case_status="completed",
            single_run_status="completed",
            cleanup_status="restored",
            evidence_status="exported",
            summary_status="collected",
            readiness_status="ok",
            verdict="detected_or_blocked",
            confidence="high",
        )
    if scenario == "case_failed":
        return SingleRunRunnerResult(
            **base,
            final_status="failed",
            case_status="failed",
            single_run_status="failed",
            cleanup_status="restored",
            evidence_status="exported",
            summary_status="collected",
            verdict="inconclusive",
            failure_kind="case_failure",
            error_summary="fake single-run case failure",
        )
    if scenario == "environment_failed":
        return SingleRunRunnerResult(
            **base,
            final_status="failed_cleanup_failed",
            case_status="stopped_environment_failure",
            single_run_status="failed",
            cleanup_status="restore_failed",
            evidence_status="failed",
            summary_status="failed",
            verdict="not_evaluable",
            failure_kind="environment_failure",
            error_summary="fake environment failure",
            unsafe_to_continue=True,
            manual_intervention_required=True,
        )
    if scenario == "timeout":
        return SingleRunRunnerResult(
            **base,
            final_status="failed",
            case_status="failed",
            single_run_status="timeout",
            cleanup_status="restored",
            evidence_status="partial",
            summary_status="missing",
            verdict="inconclusive",
            failure_kind="case_failure",
            error_summary="fake single-run timeout",
        )
    if scenario == "summary_missing":
        return SingleRunRunnerResult(
            **base,
            final_status="completed_with_warnings",
            case_status="failed",
            single_run_status="completed",
            cleanup_status="restored",
            evidence_status="exported",
            summary_status="missing",
            verdict="not_evaluable",
            failure_kind="case_failure",
            error_summary="fake case summary missing",
            warnings=("case_summary.json missing",),
        )
    if scenario == "cleanup_unknown":
        return SingleRunRunnerResult(
            **base,
            final_status="completed_with_cleanup_warning",
            case_status="stopped_environment_failure",
            single_run_status="completed",
            cleanup_status="unknown",
            evidence_status="exported",
            summary_status="collected",
            verdict="inconclusive",
            failure_kind="environment_failure",
            error_summary="fake cleanup status unknown",
            unsafe_to_continue=True,
            manual_intervention_required=True,
            warnings=("cleanup status unknown",),
        )
    if scenario == "cleanup_restore_failed":
        return SingleRunRunnerResult(
            **base,
            final_status="failed_cleanup_failed",
            case_status="stopped_environment_failure",
            single_run_status="completed",
            cleanup_status="restore_failed",
            evidence_status="exported",
            summary_status="collected",
            verdict="inconclusive",
            failure_kind="environment_failure",
            error_summary="fake cleanup restore failed",
            unsafe_to_continue=True,
            manual_intervention_required=True,
            warnings=("cleanup restore failed",),
        )
    raise MultiRunPlanError(f"unsupported fake single-run scenario: {scenario}")


def execute_multi_run_batch(
    batch_dir: Path | str,
    *,
    runner: SingleRunRunner | None = None,
) -> MultiRunState:
    root = Path(batch_dir)
    plan_path = root / "batch_plan.json"
    event_log_path = root / "multi_run_events.jsonl"
    state_path = root / "multi_run_state.json"
    plan = load_batch_plan(plan_path)
    batch_plan_sha256 = compute_bytes_sha256(plan_path.read_bytes())
    manifest_path = root / plan.sample_manifest_path
    manifest = load_sample_manifest(manifest_path)
    _ensure_batch_inputs_match(plan, manifest)

    selected_indexes = plan.selection.selected_indexes
    state = replace(
        _initial_multi_run_state(
            plan,
            manifest=manifest,
            batch_plan_sha256=batch_plan_sha256,
        ),
        batch_state="running",
    )
    write_multi_run_state(state_path, state)
    append_next_multi_run_event(
        event_log_path,
        batch_id=plan.batch_id,
        event_type="preflight_started",
        data={"selected_indexes": selected_indexes},
    )
    append_next_multi_run_event(
        event_log_path,
        batch_id=plan.batch_id,
        event_type="preflight_passed",
        data={
            "manifest_sha256": manifest.sha256,
            "batch_plan_sha256": batch_plan_sha256,
            "runner": "fake" if runner is None else type(runner).__name__,
        },
    )

    active_runner = runner or FakeSingleRunRunner()
    entries_by_index = manifest.by_index()
    cases_by_index = {case.sample_index: case for case in state.cases}
    stop_state: BatchState | None = None
    errors: list[str] = []
    warnings: list[str] = []
    unsafe_to_continue = False
    manual_intervention_required = False
    manual_intervention_reason = ""

    for index in selected_indexes:
        entry = entries_by_index[index]
        planned_case = cases_by_index[index]
        case_dir = root / "cases" / _case_dir_name(index, entry.sample_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        request = _single_run_request_for_entry(
            plan,
            entry=entry,
            case_id=planned_case.case_id,
            case_dir=case_dir,
            batch_plan_sha256=batch_plan_sha256,
        )
        append_next_multi_run_event(
            event_log_path,
            batch_id=plan.batch_id,
            event_type="case_started",
            sample_index=index,
            sample_id=entry.sample_id,
            run_id=request.run_id,
            case_id=request.case_id,
            case_status="planned",
        )
        append_next_multi_run_event(
            event_log_path,
            batch_id=plan.batch_id,
            event_type="single_run_started",
            sample_index=index,
            sample_id=entry.sample_id,
            run_id=request.run_id,
            case_id=request.case_id,
        )
        result = active_runner.run(request)
        case_state = result.to_case_state(request)
        cases_by_index[index] = case_state
        append_next_multi_run_event(
            event_log_path,
            batch_id=plan.batch_id,
            event_type="single_run_completed",
            sample_index=index,
            sample_id=entry.sample_id,
            run_id=result.run_id,
            case_id=result.case_id,
            case_status=result.case_status,
            data=result.to_dict(),
        )
        append_next_multi_run_event(
            event_log_path,
            batch_id=plan.batch_id,
            event_type="case_finalized",
            sample_index=index,
            sample_id=entry.sample_id,
            run_id=result.run_id,
            case_id=result.case_id,
            case_status=result.case_status,
            data={
                "failure_kind": result.failure_kind,
                "cleanup_status": result.cleanup_status,
                "resume_eligible": case_state.resume_eligible,
            },
        )
        if result.error_summary:
            errors.append(result.error_summary)
        warnings.extend(result.warnings)
        if result.unsafe_to_continue:
            unsafe_to_continue = True
        if result.manual_intervention_required:
            manual_intervention_required = True
            manual_intervention_reason = (
                result.error_summary or "manual intervention required"
            )

        state = replace(
            state,
            cases=_cases_in_selected_order(cases_by_index, selected_indexes),
            unsafe_to_continue=unsafe_to_continue,
            manual_intervention_required=manual_intervention_required,
            manual_intervention_reason=manual_intervention_reason,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )
        write_multi_run_state(state_path, state)

        stop_state = _stop_state_for_result(result, plan)
        if stop_state is not None:
            state = replace(
                state,
                batch_state=stop_state,
                final_status=stop_state,
                finished_at_utc=utc_now(),
            )
            write_multi_run_state(state_path, state)
            break

    if stop_state is None:
        state = replace(
            state,
            batch_state=_completed_batch_state(state),
            final_status=_completed_batch_state(state),
            finished_at_utc=utc_now(),
        )
        write_multi_run_state(state_path, state)

    append_next_multi_run_event(
        event_log_path,
        batch_id=plan.batch_id,
        event_type="batch_finished",
        data={
            "batch_state": state.batch_state,
            "final_status": state.final_status,
            "unsafe_to_continue": state.unsafe_to_continue,
            "manual_intervention_required": state.manual_intervention_required,
        },
    )
    return state


def load_batch_plan(path: Path | str) -> BatchPlan:
    plan_path = Path(path)
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MultiRunPlanError(
            f"{plan_path}: invalid batch_plan.json: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise MultiRunPlanError(f"{plan_path}: batch_plan.json must be an object")
    if payload.get("schema_version") != BATCH_PLAN_SCHEMA_VERSION:
        raise MultiRunPlanError(
            f"{plan_path}: unsupported batch plan schema_version "
            f"{payload.get('schema_version')!r}"
        )
    selection_payload = _mapping_value(payload, "selection", plan_path)
    execution_payload = _mapping_value(payload, "execution", plan_path)
    selected_indexes = tuple(
        _positive_int_from_json(value, "selection.selected_indexes", plan_path)
        for value in _list_value(selection_payload, "selected_indexes", plan_path)
    )
    selection = BatchSelection(
        mode=str(selection_payload.get("mode", "all")),
        selected_indexes=selected_indexes,
        range_text=str(selection_payload.get("range", "")),
        from_index=_optional_int(selection_payload.get("from_index"), plan_path),
        to_index=_optional_int(selection_payload.get("to_index"), plan_path),
        max_cases=_optional_int(selection_payload.get("max_cases"), plan_path),
    )
    execution = BatchExecutionPolicy(
        mode=str(execution_payload.get("mode", "serial")),
        failure_policy=str(execution_payload.get("failure_policy", "continue")),
        dry_run=bool(execution_payload.get("dry_run", False)),
        plan_only=bool(execution_payload.get("plan_only", False)),
        case_timeout_seconds=_optional_float(
            execution_payload.get("case_timeout_seconds"), plan_path
        ),
        environment_failure_policy=str(
            execution_payload.get("environment_failure_policy", "stop")
        ),
    )
    return BatchPlan(
        batch_id=_str_value(payload, "batch_id", plan_path),
        created_at_utc=_str_value(payload, "created_at_utc", plan_path),
        product_id=_str_value(payload, "product_id", plan_path),
        instance_id=_str_value(payload, "instance_id", plan_path),
        snapshot_id=_str_value(payload, "snapshot_id", plan_path),
        region=_str_value(payload, "region", plan_path),
        sample_manifest_path=_str_value(payload, "sample_manifest_path", plan_path),
        manifest_sha256=_str_value(payload, "manifest_sha256", plan_path),
        generated_config_sha256=_str_value(
            payload, "generated_config_sha256", plan_path
        ),
        selection=selection,
        execution=execution,
        single_run_runner_version=str(payload.get("single_run_runner_version", "")),
        multi_run_version=str(payload.get("multi_run_version", "")),
        product_profile_version=str(payload.get("product_profile_version", "")),
    )


def _mapping_value(
    payload: Mapping[str, Any],
    field_name: str,
    path: Path,
) -> Mapping[str, Any]:
    value = payload.get(field_name)
    if not isinstance(value, dict):
        raise MultiRunPlanError(f"{path}: {field_name} must be an object")
    return value


def _list_value(
    payload: Mapping[str, Any],
    field_name: str,
    path: Path,
) -> list[Any]:
    value = payload.get(field_name)
    if not isinstance(value, list):
        raise MultiRunPlanError(f"{path}: {field_name} must be a list")
    return value


def _str_value(payload: Mapping[str, Any], field_name: str, path: Path) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise MultiRunPlanError(f"{path}: {field_name} must be a non-empty string")
    return value


def _positive_int_from_json(value: Any, field_name: str, path: Path) -> int:
    if not isinstance(value, int) or value <= 0:
        raise MultiRunPlanError(f"{path}: {field_name} must contain positive integers")
    return value


def _optional_int(value: Any, path: Path) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise MultiRunPlanError(f"{path}: optional integer field must be an integer")
    return value


def _optional_float(value: Any, path: Path) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float):
        raise MultiRunPlanError(f"{path}: optional float field must be numeric")
    return float(value)


def _single_run_request_for_entry(
    plan: BatchPlan,
    *,
    entry: SampleManifestEntry,
    case_id: str,
    case_dir: Path,
    batch_plan_sha256: str,
) -> SingleRunRequest:
    return SingleRunRequest(
        batch_id=plan.batch_id,
        sample_index=entry.sample_index,
        sample_id=entry.sample_id,
        case_name=entry.case_name,
        case_id=case_id,
        run_id=f"{plan.batch_id}__{entry.sample_index:04d}_{entry.case_name}",
        product_id=plan.product_id,
        instance_id=plan.instance_id,
        snapshot_id=plan.snapshot_id,
        region=plan.region,
        sample_ref=entry.sample_ref,
        manifest_sha256=plan.manifest_sha256,
        batch_plan_sha256=batch_plan_sha256,
        sha256=entry.sha256,
        md5=entry.md5,
        size=entry.size,
        original_filename=entry.original_filename,
        case_dir=case_dir,
        dry_run=plan.execution.dry_run,
    )


def _case_dir_name(sample_index: int, sample_id: str) -> str:
    return f"{sample_index:04d}_{_safe_batch_id(sample_id)[:16]}"


def _cases_in_selected_order(
    cases_by_index: Mapping[int, CaseState],
    selected_indexes: tuple[int, ...],
) -> tuple[CaseState, ...]:
    return tuple(cases_by_index[index] for index in selected_indexes)


def _ensure_batch_inputs_match(
    plan: BatchPlan,
    manifest: LoadedSampleManifest,
) -> None:
    if manifest.sha256 != plan.manifest_sha256:
        raise MultiRunPlanError("sample manifest sha256 does not match batch plan")
    available = set(manifest.indexes)
    missing = [
        index for index in plan.selection.selected_indexes if index not in available
    ]
    if missing:
        formatted = ", ".join(str(index) for index in missing)
        raise MultiRunPlanError(f"batch plan selected unavailable indexes: {formatted}")


def _stop_state_for_result(
    result: SingleRunRunnerResult,
    plan: BatchPlan,
) -> BatchState | None:
    if result.failure_kind == "environment_failure" or result.unsafe_to_continue:
        return "stopped_for_environment_failure"
    if (
        result.failure_kind == "case_failure"
        and plan.execution.failure_policy == "stop-on-case-failure"
    ):
        return "stopped_for_case_failure"
    return None


def _completed_batch_state(state: MultiRunState) -> BatchState:
    case_failures = [
        case
        for case in state.cases
        if case.failure_kind == "case_failure" or case.case_status == "failed"
    ]
    if case_failures:
        return "completed_with_case_failures"
    warnings = [case for case in state.cases if case.warnings]
    if warnings or state.warnings:
        return "completed_with_warnings"
    return "completed"


def _initial_multi_run_state(
    plan: BatchPlan,
    *,
    manifest: LoadedSampleManifest,
    batch_plan_sha256: str,
) -> MultiRunState:
    entries_by_index = manifest.by_index()
    cases = tuple(
        CaseState(
            sample_index=index,
            sample_id=entries_by_index[index].sample_id,
            case_name=entries_by_index[index].case_name,
            case_id=_planned_case_id(
                index, entries_by_index[index].sample_id, plan.product_id
            ),
        )
        for index in plan.selection.selected_indexes
    )
    return MultiRunState(
        batch_id=plan.batch_id,
        batch_state="created",
        product_id=plan.product_id,
        instance_id=plan.instance_id,
        snapshot_id=plan.snapshot_id,
        region=plan.region,
        sample_manifest_path=plan.sample_manifest_path,
        manifest_sha256=plan.manifest_sha256,
        batch_plan_sha256=batch_plan_sha256,
        selected_indexes=plan.selection.selected_indexes,
        cases=cases,
        started_at_utc=plan.created_at_utc,
    )


def _planned_case_id(sample_index: int, sample_id: str, product_id: str) -> str:
    safe_sample = _safe_batch_id(sample_id)
    safe_product = _safe_batch_id(product_id).casefold()
    return f"{sample_index:04d}_{safe_sample}__{safe_product}"


def write_multi_run_state(path: Path | str, state: MultiRunState) -> None:
    _write_json_atomic(Path(path), state.to_dict())


def read_multi_run_state_payload(path: Path | str) -> dict[str, Any]:
    state_path = Path(path)
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MultiRunStateError(
            f"{state_path}: invalid multi_run_state.json: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise MultiRunStateError(
            f"{state_path}: multi_run_state.json must be an object"
        )
    schema_version = payload.get("schema_version")
    if schema_version != MULTI_RUN_STATE_SCHEMA_VERSION:
        raise MultiRunStateError(
            f"{state_path}: unsupported state schema_version {schema_version!r}"
        )
    return payload


def append_next_multi_run_event(
    path: Path | str,
    *,
    batch_id: str,
    event_type: str,
    sample_index: int | None = None,
    sample_id: str = "",
    run_id: str = "",
    case_id: str = "",
    case_status: str = "",
    data: dict[str, Any] | None = None,
) -> MultiRunEvent:
    event_path = Path(path)
    event = MultiRunEvent(
        seq=_next_event_seq(event_path),
        event_type=event_type,
        at_utc=utc_now(),
        batch_id=batch_id,
        sample_index=sample_index,
        sample_id=sample_id,
        run_id=run_id,
        case_id=case_id,
        case_status=case_status,
        data=data or {},
    )
    append_multi_run_event(event_path, event)
    return event


def append_multi_run_event(path: Path | str, event: MultiRunEvent) -> None:
    event_path = Path(path)
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with event_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def read_multi_run_events(path: Path | str) -> tuple[dict[str, Any], ...]:
    event_path = Path(path)
    if not event_path.exists():
        return ()
    events: list[dict[str, Any]] = []
    with event_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MultiRunStateError(
                    f"{event_path}: line {line_number}: invalid event json: {exc.msg}"
                ) from exc
            if not isinstance(payload, dict):
                raise MultiRunStateError(
                    f"{event_path}: line {line_number}: event must be an object"
                )
            if payload.get("schema_version") != MULTI_RUN_EVENT_SCHEMA_VERSION:
                raise MultiRunStateError(
                    f"{event_path}: line {line_number}: unsupported event schema_version"
                )
            events.append(payload)
    return tuple(events)


def _next_event_seq(path: Path) -> int:
    events = read_multi_run_events(path)
    if not events:
        return 1
    seq_values = [event.get("seq") for event in events]
    int_values = [value for value in seq_values if isinstance(value, int)]
    if len(int_values) != len(seq_values):
        raise MultiRunStateError(f"{path}: event seq must be an integer")
    return max(int_values, default=0) + 1


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(path)


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
