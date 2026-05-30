from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypeAlias

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


def compute_text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
class MultiRunPlanArtifacts:
    batch_dir: Path
    batch_plan_path: Path
    generated_config_path: Path
    manifest_sha256_path: Path
    batch_plan: BatchPlan

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_dir": str(self.batch_dir),
            "batch_plan_path": str(self.batch_plan_path),
            "generated_config_path": str(self.generated_config_path),
            "manifest_sha256_path": str(self.manifest_sha256_path),
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
) -> MultiRunPlanArtifacts:
    resolved_batch_id = _safe_batch_id(batch_id or default_batch_id(product_id))
    batch_dir = Path(batch_root) / resolved_batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    generated_config = render_multi_run_generated_config(
        product_id=product_id,
        instance_id=instance_id,
        snapshot_id=snapshot_id,
        region=region,
        guest_agent_url=guest_agent_url,
        desktop_worker_url=desktop_worker_url,
        dry_run=dry_run,
    )
    generated_config_sha256 = compute_text_sha256(generated_config)
    plan = BatchPlan(
        batch_id=resolved_batch_id,
        created_at_utc=utc_now(),
        product_id=product_id,
        instance_id=instance_id,
        snapshot_id=snapshot_id,
        region=region,
        sample_manifest_path=str(manifest.path),
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
        ),
    )

    batch_plan_path = batch_dir / "batch_plan.json"
    generated_config_path = batch_dir / "multi_run.generated.toml"
    manifest_sha256_path = batch_dir / "sample_manifest.sha256"

    batch_plan_path.write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    generated_config_path.write_text(generated_config, encoding="utf-8")
    manifest_sha256_path.write_text(
        f"{manifest.sha256}  {manifest.path.name}\n",
        encoding="utf-8",
    )

    return MultiRunPlanArtifacts(
        batch_dir=batch_dir,
        batch_plan_path=batch_plan_path,
        generated_config_path=generated_config_path,
        manifest_sha256_path=manifest_sha256_path,
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
            f"dry_run = {_toml_bool(dry_run)}",
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
