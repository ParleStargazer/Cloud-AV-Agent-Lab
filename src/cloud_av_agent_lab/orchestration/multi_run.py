from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias
from urllib.parse import urlparse

SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION = "multi-run-sample.v1"
BATCH_PLAN_SCHEMA_VERSION = "multi-run-plan.v1"
MULTI_RUN_STATE_SCHEMA_VERSION = "multi-run-state.v1"
MULTI_RUN_EVENT_SCHEMA_VERSION = "multi-run-event.v1"
MULTI_RUN_AGGREGATE_SUMMARY_SCHEMA_VERSION = "multi-run-aggregate-summary.v1"
MULTI_RUN_PREFLIGHT_REPORT_SCHEMA_VERSION = "multi-run-preflight-report.v1"
MULTI_RUN_VERSION = "multi-run.v1"
DEFAULT_MULTI_RUN_SETTLING_COOLDOWN_SECONDS = 15.0
DEFAULT_MULTI_RUN_UPLOAD_STATUS_TIMEOUT_SECONDS = 30.0
DEFAULT_MULTI_RUN_POST_EXECUTION_COLLECTION_DELAY_SECONDS = 45.0
DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS = 1.0
DEFAULT_MULTI_RUN_POST_EXECUTION_QUARANTINE_DELAY_SECONDS = 3.0
IGNORED_SAMPLE_INDEX_FILENAMES = frozenset(
    {".gitignore", ".gitkeep", "readme", "readme.md", "readme.txt"}
)
IGNORED_SAMPLE_INDEX_DIR_NAMES = frozenset(
    {".git", "__pycache__", "indexed", "runs", "sample_index"}
)

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
    "deferred_to_next_case",
    "unknown",
    "not_started",
    "skipped",
    "not_required",
]
IndexedSampleState: TypeAlias = Literal[
    "available",
    "burned",
    "burn_failed",
    "not_required",
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
MultiRunExecutionMode: TypeAlias = Literal[
    "run",
    "resume",
    "rerun_failed",
    "force_rerun",
]
BatchState: TypeAlias = Literal[
    "created",
    "planning",
    "manifest_ready",
    "lightweight_preflight_passed",
    "failed_preflight",
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
PreflightCheckStatus: TypeAlias = Literal["passed", "failed", "skipped"]
CleanupStrategy: TypeAlias = Literal["per_case", "deferred"]
BatchCleanupStatus: TypeAlias = Literal[
    "not_started",
    "restored",
    "restore_failed",
    "not_required",
]
EmergencyPoweroffStatus: TypeAlias = Literal[
    "not_started",
    "not_needed",
    "attempted",
]

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
    "deferred_to_next_case",
    "unknown",
    "not_started",
    "skipped",
    "not_required",
)
INDEXED_SAMPLE_STATES: tuple[str, ...] = (
    "available",
    "burned",
    "burn_failed",
    "not_required",
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
    "lightweight_preflight_passed",
    "failed_preflight",
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
CLEANUP_STRATEGIES: tuple[str, ...] = ("per_case", "deferred")
BATCH_CLEANUP_STATUSES: tuple[str, ...] = (
    "not_started",
    "restored",
    "restore_failed",
    "not_required",
)
EMERGENCY_POWEROFF_STATUSES: tuple[str, ...] = (
    "not_started",
    "not_needed",
    "attempted",
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


@dataclass(frozen=True)
class ProductProbeAvailability:
    available: bool
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "skip_reason": self.skip_reason,
        }


def resolve_product_probe_availability(
    product_id: str,
    supported_products: Iterable[str],
) -> ProductProbeAvailability:
    normalized_product = str(product_id or "").strip().casefold()
    supported = {str(item or "").strip().casefold() for item in supported_products}
    if normalized_product and normalized_product in supported:
        return ProductProbeAvailability(available=True)
    return ProductProbeAvailability(
        available=False,
        skip_reason="product_probe_not_registered",
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
class LoadedSampleManifest:
    path: Path
    sha256: str
    entries: tuple[SampleManifestEntry, ...]

    @property
    def indexes(self) -> tuple[int, ...]:
        return tuple(entry.sample_index for entry in self.entries)

    def by_index(self) -> dict[int, SampleManifestEntry]:
        return {entry.sample_index: entry for entry in self.entries}


@dataclass(frozen=True)
class SampleManifestIndexArtifacts:
    sample_dir: Path
    output_dir: Path
    indexed_dir: Path
    manifest_path: Path
    sample_name_map_path: Path
    entries: tuple[SampleManifestEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_dir": str(self.sample_dir),
            "output_dir": str(self.output_dir),
            "indexed_dir": str(self.indexed_dir),
            "manifest_path": str(self.manifest_path),
            "sample_name_map_path": str(self.sample_name_map_path),
            "entries": [entry.to_dict() for entry in self.entries],
        }


@dataclass(frozen=True)
class _IndexedSampleCandidate:
    path: Path
    relative_path: str
    sha256: str
    md5: str
    size: int
    original_filename: str
    original_suffix: str


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


def build_sample_manifest_from_directory(
    sample_dir: Path | str,
    output_dir: Path | str,
    *,
    manifest_id: str = "",
    created_at_utc: str | None = None,
) -> SampleManifestIndexArtifacts:
    source_root = Path(sample_dir)
    if not source_root.is_dir():
        raise MultiRunManifestError(f"sample_dir does not exist: {source_root}")
    destination_root = Path(output_dir)
    indexed_dir = destination_root / "indexed"
    manifest_path = destination_root / "sample_manifest.jsonl"
    name_map_path = destination_root / "sample_name_map.txt"
    _ensure_index_output_paths_do_not_exist(indexed_dir, manifest_path, name_map_path)
    destination_root.mkdir(parents=True, exist_ok=True)
    indexed_dir.mkdir(parents=True, exist_ok=True)

    candidates = _scan_sample_candidates(source_root)
    if not candidates:
        raise MultiRunManifestError("sample_dir contains no regular sample files")
    grouped: dict[str, list[_IndexedSampleCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.sha256, []).append(candidate)
    unique_sha256 = tuple(sorted(grouped))
    prefixes = unique_sha_prefixes(unique_sha256)
    timestamp = created_at_utc or utc_now()
    resolved_manifest_id = manifest_id or f"manifest-{timestamp}"
    entries: list[SampleManifestEntry] = []
    name_map_lines = ["# original_relative_path\trenamed_filename\tsha256\tprimary\n"]
    for sample_index, sha256 in enumerate(unique_sha256, start=1):
        duplicates = sorted(grouped[sha256], key=lambda item: item.relative_path)
        primary = duplicates[0]
        prefix = prefixes[sha256]
        renamed_filename = f"{sample_index:04d}_{prefix}{primary.original_suffix}"
        indexed_path = indexed_dir / renamed_filename
        shutil.copy2(primary.path, indexed_path)
        aliases = tuple(candidate.relative_path for candidate in duplicates)
        entry = SampleManifestEntry(
            sample_index=sample_index,
            sample_id=sha256,
            case_name=prefix,
            sha256=sha256,
            md5=primary.md5,
            size=primary.size,
            original_filename=primary.original_filename,
            original_suffix=primary.original_suffix,
            normalized_suffix=primary.original_suffix.casefold(),
            renamed_filename=renamed_filename,
            sample_ref=indexed_path.as_posix(),
            manifest_id=resolved_manifest_id,
            manifest_created_at_utc=timestamp,
            manifest_tool_version=MULTI_RUN_VERSION,
            sample_source_kind="local_platform_path",
            duplicate_group_id=f"sha256:{sha256}",
            duplicate_of_sample_index=None,
            aliases=aliases,
            entry_status="ready",
            skip_reason=None,
            created_at_utc=timestamp,
        )
        entries.append(entry)
        for candidate in duplicates:
            name_map_lines.append(
                "\t".join(
                    [
                        candidate.relative_path,
                        renamed_filename,
                        sha256,
                        "true" if candidate == primary else "false",
                    ]
                )
                + "\n"
            )

    manifest_path.write_text(
        "".join(
            json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for entry in entries
        ),
        encoding="utf-8",
    )
    name_map_path.write_text("".join(name_map_lines), encoding="utf-8")
    return SampleManifestIndexArtifacts(
        sample_dir=source_root,
        output_dir=destination_root,
        indexed_dir=indexed_dir,
        manifest_path=manifest_path,
        sample_name_map_path=name_map_path,
        entries=tuple(entries),
    )


def unique_sha_prefixes(
    sha256_values: Iterable[str],
    *,
    min_length: int = 16,
) -> dict[str, str]:
    values = tuple(sorted(set(sha.casefold() for sha in sha256_values)))
    for sha in values:
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            raise MultiRunManifestError(f"invalid sha256 for prefixing: {sha!r}")
    prefixes: dict[str, str] = {}
    for sha in values:
        prefix_length = min_length
        while prefix_length <= 64:
            prefix = sha[:prefix_length]
            if sum(other.startswith(prefix) for other in values) == 1:
                prefixes[sha] = prefix
                break
            prefix_length += 1
        else:
            prefixes[sha] = sha
    return prefixes


def _scan_sample_candidates(sample_dir: Path) -> tuple[_IndexedSampleCandidate, ...]:
    candidates: list[_IndexedSampleCandidate] = []
    for root, dir_names, file_names in os.walk(sample_dir, followlinks=False):
        root_path = Path(root)
        dir_names[:] = [
            name
            for name in dir_names
            if not _should_skip_index_path(root_path / name, sample_dir)
        ]
        for file_name in sorted(file_names):
            path = root_path / file_name
            if _should_skip_index_path(path, sample_dir):
                continue
            file_stat = path.stat()
            if not stat.S_ISREG(file_stat.st_mode):
                continue
            digest = _hash_sample_file(path)
            candidates.append(
                _IndexedSampleCandidate(
                    path=path,
                    relative_path=path.relative_to(sample_dir).as_posix(),
                    sha256=digest["sha256"],
                    md5=digest["md5"],
                    size=digest["size"],
                    original_filename=path.name,
                    original_suffix=path.suffix.casefold(),
                )
            )
    return tuple(sorted(candidates, key=lambda item: item.relative_path))


def _should_skip_index_path(path: Path, sample_dir: Path) -> bool:
    if path.is_symlink():
        return True
    normalized_name = path.name.casefold()
    if normalized_name.startswith("."):
        return True
    if path.is_dir():
        if normalized_name in IGNORED_SAMPLE_INDEX_DIR_NAMES:
            return True
    elif normalized_name in IGNORED_SAMPLE_INDEX_FILENAMES:
        return True
    if ":" in path.name:
        return True
    try:
        resolved = path.resolve()
        resolved_sample_dir = sample_dir.resolve()
        if resolved != resolved_sample_dir and not _is_relative_path(
            resolved, resolved_sample_dir
        ):
            return True
        file_stat = path.stat()
    except OSError:
        return True
    attributes = getattr(file_stat, "st_file_attributes", 0)
    return bool(attributes & 0x400)


def _is_relative_path(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _hash_sample_file(path: Path) -> dict[str, Any]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            sha256.update(chunk)
            md5.update(chunk)
    return {"sha256": sha256.hexdigest(), "md5": md5.hexdigest(), "size": size}


def _ensure_index_output_paths_do_not_exist(*paths: Path) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise MultiRunManifestError(
            "sample manifest index output already exists: " + ", ".join(existing)
        )


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
    fastmode: bool = False
    case_timeout_seconds: float | None = None
    settling_cooldown_seconds: float = DEFAULT_MULTI_RUN_SETTLING_COOLDOWN_SECONDS
    upload_status_timeout_seconds: float = (
        DEFAULT_MULTI_RUN_UPLOAD_STATUS_TIMEOUT_SECONDS
    )
    post_execution_collection_delay_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_COLLECTION_DELAY_SECONDS
    )
    product_probe_enabled: bool = False
    post_execution_probe_interval_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS
    )
    product_probe_available: bool = False
    product_probe_skip_reason: str = ""
    execution_product_probe_enabled: bool = False
    execution_product_probe_interval_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS
    )
    post_execution_quarantine_delay_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_QUARANTINE_DELAY_SECONDS
    )
    environment_failure_policy: str = "stop"
    cleanup_strategy: CleanupStrategy = "per_case"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "failure_policy": self.failure_policy,
            "dry_run": self.dry_run,
            "plan_only": self.plan_only,
            "fastmode": self.fastmode,
            "case_timeout_seconds": self.case_timeout_seconds,
            "settling_cooldown_seconds": self.settling_cooldown_seconds,
            "upload_status_timeout_seconds": self.upload_status_timeout_seconds,
            "post_execution_collection_delay_seconds": (
                self.post_execution_collection_delay_seconds
            ),
            "product_probe_enabled": self.product_probe_enabled,
            "post_execution_probe_interval_seconds": (
                self.post_execution_probe_interval_seconds
            ),
            "product_probe_available": self.product_probe_available,
            "product_probe_skip_reason": self.product_probe_skip_reason,
            "execution_product_probe_enabled": (self.execution_product_probe_enabled),
            "execution_product_probe_interval_seconds": (
                self.execution_product_probe_interval_seconds
            ),
            "post_execution_quarantine_delay_seconds": (
                self.post_execution_quarantine_delay_seconds
            ),
            "environment_failure_policy": self.environment_failure_policy,
            "cleanup_strategy": self.cleanup_strategy,
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
class MultiRunAggregateArtifacts:
    summary_json_path: Path
    summary_markdown_path: Path
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_json_path": str(self.summary_json_path),
            "summary_markdown_path": str(self.summary_markdown_path),
            "summary": _jsonable(self.summary),
        }


@dataclass(frozen=True)
class MultiRunPreflightCheck:
    name: str
    status: PreflightCheckStatus
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "data": _jsonable(dict(self.data)),
        }


@dataclass(frozen=True)
class MultiRunPreflightReport:
    batch_id: str
    generated_at_utc: str
    checks: tuple[MultiRunPreflightCheck, ...]
    schema_version: str = MULTI_RUN_PREFLIGHT_REPORT_SCHEMA_VERSION

    @property
    def passed(self) -> bool:
        return all(check.status != "failed" for check in self.checks)

    @property
    def failed_checks(self) -> tuple[MultiRunPreflightCheck, ...]:
        return tuple(check for check in self.checks if check.status == "failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "generated_at_utc": self.generated_at_utc,
            "status": "passed" if self.passed else "failed",
            "checks": [check.to_dict() for check in self.checks],
        }


class MultiRunPreflightChecker(Protocol):
    def check_guest_agent_url(self, url: str) -> MultiRunPreflightCheck:
        """Return the Guest Agent reachability check result."""

    def check_desktop_worker_url(self, url: str) -> MultiRunPreflightCheck:
        """Return the Desktop Worker reachability check result."""


class StaticMultiRunPreflightChecker:
    """Safe default checker that does not perform network I/O."""

    def check_guest_agent_url(self, url: str) -> MultiRunPreflightCheck:
        return MultiRunPreflightCheck(
            name="guest_agent_reachability",
            status="skipped",
            message=(
                "network reachability is deferred; static preflight only validates "
                "URL shape"
            ),
            data={"url_configured": bool(url), "network_io": False},
        )

    def check_desktop_worker_url(self, url: str) -> MultiRunPreflightCheck:
        return MultiRunPreflightCheck(
            name="desktop_worker_reachability",
            status="skipped",
            message=(
                "network reachability is deferred; static preflight only validates "
                "URL shape"
            ),
            data={"url_configured": bool(url), "network_io": False},
        )


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
    guest_agent_url: str = ""
    desktop_worker_url: str = ""
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
            "guest_agent_url": self.guest_agent_url,
            "desktop_worker_url": self.desktop_worker_url,
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
    cleanup_strategy: CleanupStrategy = "per_case",
    fastmode: bool = False,
    settling_cooldown_seconds: float = DEFAULT_MULTI_RUN_SETTLING_COOLDOWN_SECONDS,
    upload_status_timeout_seconds: float = (
        DEFAULT_MULTI_RUN_UPLOAD_STATUS_TIMEOUT_SECONDS
    ),
    post_execution_collection_delay_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_COLLECTION_DELAY_SECONDS
    ),
    product_probe_enabled: bool = False,
    post_execution_probe_interval_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS
    ),
    product_probe_available: bool = False,
    product_probe_skip_reason: str = "",
    execution_product_probe_enabled: bool = False,
    execution_product_probe_interval_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS
    ),
    post_execution_quarantine_delay_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_QUARANTINE_DELAY_SECONDS
    ),
) -> MultiRunPlanArtifacts:
    resolved_batch_id = _safe_batch_id(batch_id or default_batch_id(product_id))
    if cleanup_strategy not in CLEANUP_STRATEGIES:
        allowed = ", ".join(CLEANUP_STRATEGIES)
        raise MultiRunPlanError(f"cleanup_strategy must be one of: {allowed}")
    _ensure_non_negative_delay("settling_cooldown_seconds", settling_cooldown_seconds)
    _ensure_non_negative_delay(
        "upload_status_timeout_seconds", upload_status_timeout_seconds
    )
    _ensure_non_negative_delay(
        "post_execution_collection_delay_seconds",
        post_execution_collection_delay_seconds,
    )
    _ensure_non_negative_delay(
        "post_execution_probe_interval_seconds",
        post_execution_probe_interval_seconds,
    )
    _ensure_non_negative_delay(
        "execution_product_probe_interval_seconds",
        execution_product_probe_interval_seconds,
    )
    _ensure_non_negative_delay(
        "post_execution_quarantine_delay_seconds",
        post_execution_quarantine_delay_seconds,
    )
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
        cleanup_strategy=cleanup_strategy,
        fastmode=fastmode,
        settling_cooldown_seconds=settling_cooldown_seconds,
        upload_status_timeout_seconds=upload_status_timeout_seconds,
        post_execution_collection_delay_seconds=(
            post_execution_collection_delay_seconds
        ),
        product_probe_enabled=product_probe_enabled,
        post_execution_probe_interval_seconds=post_execution_probe_interval_seconds,
        product_probe_available=product_probe_available,
        product_probe_skip_reason=product_probe_skip_reason,
        execution_product_probe_enabled=execution_product_probe_enabled,
        execution_product_probe_interval_seconds=(
            execution_product_probe_interval_seconds
        ),
        post_execution_quarantine_delay_seconds=(
            post_execution_quarantine_delay_seconds
        ),
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
        guest_agent_url=guest_agent_url,
        desktop_worker_url=desktop_worker_url,
        single_run_runner_version="single-run.v1",
        multi_run_version=MULTI_RUN_VERSION,
        product_profile_version=f"{product_id}.v1",
        selection=selection,
        execution=BatchExecutionPolicy(
            mode="serial",
            failure_policy=failure_policy,
            dry_run=dry_run,
            plan_only=plan_only,
            fastmode=fastmode,
            settling_cooldown_seconds=settling_cooldown_seconds,
            upload_status_timeout_seconds=upload_status_timeout_seconds,
            post_execution_collection_delay_seconds=(
                post_execution_collection_delay_seconds
            ),
            product_probe_enabled=product_probe_enabled,
            post_execution_probe_interval_seconds=post_execution_probe_interval_seconds,
            product_probe_available=product_probe_available,
            product_probe_skip_reason=product_probe_skip_reason,
            execution_product_probe_enabled=execution_product_probe_enabled,
            execution_product_probe_interval_seconds=(
                execution_product_probe_interval_seconds
            ),
            post_execution_quarantine_delay_seconds=(
                post_execution_quarantine_delay_seconds
            ),
            cleanup_strategy=cleanup_strategy,
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
            "cleanup_strategy": cleanup_strategy,
            "fastmode": fastmode,
            "settling_cooldown_seconds": settling_cooldown_seconds,
            "upload_status_timeout_seconds": upload_status_timeout_seconds,
            "post_execution_collection_delay_seconds": (
                post_execution_collection_delay_seconds
            ),
            "product_probe_enabled": product_probe_enabled,
            "post_execution_probe_interval_seconds": (
                post_execution_probe_interval_seconds
            ),
            "product_probe_available": product_probe_available,
            "product_probe_skip_reason": product_probe_skip_reason,
            "execution_product_probe_enabled": execution_product_probe_enabled,
            "execution_product_probe_interval_seconds": (
                execution_product_probe_interval_seconds
            ),
            "post_execution_quarantine_delay_seconds": (
                post_execution_quarantine_delay_seconds
            ),
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


def run_multi_run_preflight(
    batch_dir: Path | str,
    *,
    plan: BatchPlan,
    manifest: LoadedSampleManifest,
    batch_plan_sha256: str,
    runner: SingleRunRunner,
    checker: MultiRunPreflightChecker | None = None,
) -> MultiRunPreflightReport:
    root = Path(batch_dir)
    active_checker = checker or StaticMultiRunPreflightChecker()
    checks = [
        _check_batch_directory_writable(root),
        _check_manifest_digest(plan, manifest),
        _check_selected_indexes(plan, manifest),
        _check_runner_callable(runner),
        _check_product_profile(plan.product_id),
        _check_cloud_identifier("instance_id", plan.instance_id),
        _check_cloud_identifier("snapshot_id", plan.snapshot_id),
        _check_region(plan.region),
        _check_http_url("guest_agent_url", plan.guest_agent_url),
        _check_http_url("desktop_worker_url", plan.desktop_worker_url),
        _safe_preflight_call(
            "guest_agent_reachability",
            lambda: active_checker.check_guest_agent_url(plan.guest_agent_url),
        ),
        _safe_preflight_call(
            "desktop_worker_reachability",
            lambda: active_checker.check_desktop_worker_url(plan.desktop_worker_url),
        ),
        _check_instance_lock_available(root, plan.instance_id, plan.batch_id),
        _check_evidence_output_writable(root),
        _check_generated_config_has_no_secrets(root / "multi_run.generated.toml"),
        _check_batch_plan_sha256(batch_plan_sha256),
    ]
    return MultiRunPreflightReport(
        batch_id=plan.batch_id,
        generated_at_utc=utc_now(),
        checks=tuple(checks),
    )


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
    cleanup_strategy: CleanupStrategy = "per_case",
    fastmode: bool = False,
    settling_cooldown_seconds: float = DEFAULT_MULTI_RUN_SETTLING_COOLDOWN_SECONDS,
    upload_status_timeout_seconds: float = (
        DEFAULT_MULTI_RUN_UPLOAD_STATUS_TIMEOUT_SECONDS
    ),
    post_execution_collection_delay_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_COLLECTION_DELAY_SECONDS
    ),
    product_probe_enabled: bool = False,
    post_execution_probe_interval_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS
    ),
    product_probe_available: bool = False,
    product_probe_skip_reason: str = "",
    execution_product_probe_enabled: bool = False,
    execution_product_probe_interval_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS
    ),
    post_execution_quarantine_delay_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_QUARANTINE_DELAY_SECONDS
    ),
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
            f"cleanup_strategy = {_toml_string(cleanup_strategy)}",
            f"fastmode = {_toml_bool(fastmode)}",
            f"settling_cooldown_seconds = {float(settling_cooldown_seconds):g}",
            f"upload_status_timeout_seconds = {float(upload_status_timeout_seconds):g}",
            "post_execution_collection_delay_seconds = "
            f"{float(post_execution_collection_delay_seconds):g}",
            f"product_probe_enabled = {_toml_bool(product_probe_enabled)}",
            "post_execution_probe_interval_seconds = "
            f"{float(post_execution_probe_interval_seconds):g}",
            f"product_probe_available = {_toml_bool(product_probe_available)}",
            f"product_probe_skip_reason = {_toml_string(product_probe_skip_reason)}",
            "execution_product_probe_enabled = "
            f"{_toml_bool(execution_product_probe_enabled)}",
            "execution_product_probe_interval_seconds = "
            f"{float(execution_product_probe_interval_seconds):g}",
            "post_execution_quarantine_delay_seconds = "
            f"{float(post_execution_quarantine_delay_seconds):g}",
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


def _ensure_non_negative_delay(name: str, value: float) -> None:
    if value < 0:
        raise MultiRunPlanError(f"{name} must be greater than or equal to 0")


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
    indexed_sample_state: IndexedSampleState = "available"
    evidence_status: EvidenceStatus = "not_started"
    summary_status: SummaryStatus = "not_started"
    readiness_status: ReadinessStatus = "unknown"
    resume_eligible: bool = False
    verdict: Verdict = "unknown"
    confidence: str = ""
    failure_kind: FailureKind | None = None
    result_source: str = ""
    simulated: bool = False
    error_summary: str = ""
    evidence_bundle_path: str = ""
    run_state_path: str = ""
    case_summary_path: str = ""
    duration_seconds: float | None = None
    timing: dict[str, Any] = field(default_factory=dict)
    fastmode_eligible: bool = False
    fastmode_reason: str = ""
    fastmode_used: bool = False
    environment_reused_from_case_id: str = ""
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
            "indexed_sample_state": self.indexed_sample_state,
            "evidence_status": self.evidence_status,
            "summary_status": self.summary_status,
            "readiness_status": self.readiness_status,
            "resume_eligible": self.resume_eligible,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "failure_kind": self.failure_kind,
            "result_source": self.result_source,
            "simulated": self.simulated,
            "error_summary": self.error_summary,
            "evidence_bundle_path": self.evidence_bundle_path,
            "run_state_path": self.run_state_path,
            "case_summary_path": self.case_summary_path,
            "duration_seconds": self.duration_seconds,
            "timing": _jsonable(self.timing),
            "fastmode_eligible": self.fastmode_eligible,
            "fastmode_reason": self.fastmode_reason,
            "fastmode_used": self.fastmode_used,
            "environment_reused_from_case_id": self.environment_reused_from_case_id,
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
    batch_cleanup_status: BatchCleanupStatus = "not_started"
    emergency_poweroff_status: EmergencyPoweroffStatus = "not_started"
    fastmode_enabled: bool = False
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
            "batch_cleanup_status": self.batch_cleanup_status,
            "emergency_poweroff_status": self.emergency_poweroff_status,
            "fastmode_enabled": self.fastmode_enabled,
            "timing": _build_multi_run_timing_summary(self.cases),
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
    guest_agent_url: str
    desktop_worker_url: str
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
    defer_final_cleanup: bool = False
    skip_initial_restore: bool = False
    environment_reused_from_case_id: str = ""
    settling_cooldown_seconds: float = DEFAULT_MULTI_RUN_SETTLING_COOLDOWN_SECONDS
    upload_status_timeout_seconds: float = (
        DEFAULT_MULTI_RUN_UPLOAD_STATUS_TIMEOUT_SECONDS
    )
    post_execution_collection_delay_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_COLLECTION_DELAY_SECONDS
    )
    product_probe_enabled: bool = False
    post_execution_probe_interval_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS
    )
    product_probe_available: bool = False
    product_probe_skip_reason: str = ""
    execution_product_probe_enabled: bool = False
    execution_product_probe_interval_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS
    )
    post_execution_quarantine_delay_seconds: float = (
        DEFAULT_MULTI_RUN_POST_EXECUTION_QUARANTINE_DELAY_SECONDS
    )

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
            "guest_agent_url": self.guest_agent_url,
            "desktop_worker_url": self.desktop_worker_url,
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
            "defer_final_cleanup": self.defer_final_cleanup,
            "skip_initial_restore": self.skip_initial_restore,
            "environment_reused_from_case_id": self.environment_reused_from_case_id,
            "settling_cooldown_seconds": self.settling_cooldown_seconds,
            "upload_status_timeout_seconds": self.upload_status_timeout_seconds,
            "post_execution_collection_delay_seconds": (
                self.post_execution_collection_delay_seconds
            ),
            "product_probe_enabled": self.product_probe_enabled,
            "post_execution_probe_interval_seconds": (
                self.post_execution_probe_interval_seconds
            ),
            "product_probe_available": self.product_probe_available,
            "product_probe_skip_reason": self.product_probe_skip_reason,
            "execution_product_probe_enabled": (self.execution_product_probe_enabled),
            "execution_product_probe_interval_seconds": (
                self.execution_product_probe_interval_seconds
            ),
            "post_execution_quarantine_delay_seconds": (
                self.post_execution_quarantine_delay_seconds
            ),
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
    indexed_sample_state: IndexedSampleState = "available"
    readiness_status: ReadinessStatus = "unknown"
    verdict: Verdict = "unknown"
    confidence: str = ""
    failure_kind: FailureKind | None = None
    result_source: str = ""
    simulated: bool = False
    error_summary: str = ""
    evidence_bundle_path: str = ""
    run_state_path: str = ""
    case_summary_path: str = ""
    duration_seconds: float | None = None
    timing: dict[str, Any] = field(default_factory=dict)
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
            "indexed_sample_state": self.indexed_sample_state,
            "evidence_status": self.evidence_status,
            "summary_status": self.summary_status,
            "readiness_status": self.readiness_status,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "failure_kind": classify_runner_result(self),
            "result_source": self.result_source,
            "simulated": self.simulated,
            "error_summary": self.error_summary,
            "evidence_bundle_path": self.evidence_bundle_path,
            "run_state_path": self.run_state_path,
            "case_summary_path": self.case_summary_path,
            "duration_seconds": self.duration_seconds,
            "timing": _jsonable(self.timing),
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
            indexed_sample_state=self.indexed_sample_state,
            evidence_status=self.evidence_status,
            summary_status=self.summary_status,
            readiness_status=self.readiness_status,
            resume_eligible=_case_resume_eligible(self),
            verdict=self.verdict,
            confidence=self.confidence,
            failure_kind=classify_runner_result(self),
            result_source=self.result_source,
            simulated=self.simulated,
            error_summary=self.error_summary,
            evidence_bundle_path=self.evidence_bundle_path,
            run_state_path=self.run_state_path,
            case_summary_path=self.case_summary_path,
            duration_seconds=self.duration_seconds,
            timing=self.timing,
            fastmode_used=request.skip_initial_restore,
            environment_reused_from_case_id=request.environment_reused_from_case_id,
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


SingleRunEntrypoint = Callable[[Any], Any]


class RealSingleRunRunner:
    def __init__(
        self,
        *,
        run_single_case_func: SingleRunEntrypoint | None = None,
    ) -> None:
        self._run_single_case_func = run_single_case_func
        self.requests: list[SingleRunRequest] = []

    def run(self, request: SingleRunRequest) -> SingleRunRunnerResult:
        self.requests.append(request)
        try:
            _validate_single_run_sample_ref(request)
            from .single_run import SingleRunOptions, run_single_case

            entrypoint = self._run_single_case_func or run_single_case
            result = entrypoint(
                SingleRunOptions(
                    instance_id=request.instance_id,
                    snapshot_id=request.snapshot_id,
                    region=request.region,
                    sample_name=request.case_name,
                    sample_path=Path(request.sample_ref),
                    guest_agent_url=request.guest_agent_url,
                    product_id=request.product_id,
                    desktop_worker_url=request.desktop_worker_url,
                    dry_run=request.dry_run,
                    defer_final_cleanup=request.defer_final_cleanup,
                    skip_initial_restore=request.skip_initial_restore,
                    settling_cooldown_seconds=request.settling_cooldown_seconds,
                    upload_poll_timeout_seconds=(request.upload_status_timeout_seconds),
                    post_execution_collection_delay_seconds=(
                        request.post_execution_collection_delay_seconds
                    ),
                    product_probe_enabled=request.product_probe_enabled,
                    post_execution_probe_interval_seconds=(
                        request.post_execution_probe_interval_seconds
                    ),
                    product_probe_available=request.product_probe_available,
                    product_probe_skip_reason=request.product_probe_skip_reason,
                    execution_product_probe_enabled=(
                        request.execution_product_probe_enabled
                    ),
                    execution_product_probe_interval_seconds=(
                        request.execution_product_probe_interval_seconds
                    ),
                    post_execution_quarantine_delay_seconds=(
                        request.post_execution_quarantine_delay_seconds
                    ),
                    runs_dir=request.case_dir,
                )
            )
            return _single_run_result_to_runner_result(request, result)
        except Exception as exc:  # noqa: BLE001 - normalize runner failures for batch state.
            return SingleRunRunnerResult(
                run_id=request.run_id,
                case_id=request.case_id,
                final_status="failed",
                case_status="failed",
                single_run_status="failed",
                cleanup_status="not_started",
                evidence_status="not_started",
                summary_status="not_started",
                verdict="inconclusive",
                failure_kind="case_failure",
                result_source="single_run_runner",
                simulated=request.dry_run,
                error_summary=_safe_multi_run_message(exc),
                warnings=("single-run runner failed before returning a result",),
            )


def _runner_result_with_scheduler_timing(
    result: SingleRunRunnerResult,
    *,
    elapsed_seconds: float,
) -> SingleRunRunnerResult:
    scheduler_duration = _round_duration_seconds(elapsed_seconds)
    timing = dict(result.timing)
    timing.setdefault("schema_version", "multi-run-case-timing.v1")
    timing["scheduler_duration_seconds"] = scheduler_duration
    duration_seconds = result.duration_seconds
    if duration_seconds is None:
        duration_seconds = scheduler_duration
    timing.setdefault("total_seconds", _round_duration_seconds(duration_seconds))
    return replace(result, duration_seconds=duration_seconds, timing=timing)


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
        "timing": {
            "schema_version": "multi-run-case-timing.v1",
            "total_seconds": 1.0,
            "stages": {"fake_runner": 1.0},
            "steps": [
                {
                    "name": "fake_runner",
                    "status": "ok",
                    "duration_seconds": 1.0,
                }
            ],
        },
        "result_source": "fake_runner",
        "simulated": True,
    }
    if scenario == "completed":
        return SingleRunRunnerResult(
            **base,
            final_status="completed",
            case_status="completed",
            single_run_status="completed",
            cleanup_status=(
                "deferred_to_next_case" if request.defer_final_cleanup else "restored"
            ),
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


def _validate_single_run_sample_ref(request: SingleRunRequest) -> None:
    path = Path(request.sample_ref)
    if not path.is_file():
        raise MultiRunPlanError(f"sample_ref does not exist: {path}")
    if not request.dry_run and path.parent.name != "indexed":
        raise MultiRunPlanError(
            "non-dry-run single-run requires sample_ref from an indexed sample mirror"
        )
    digest = _hash_sample_file(path)
    mismatches = [
        field_name
        for field_name, expected_value in (
            ("sha256", request.sha256),
            ("md5", request.md5),
            ("size", request.size),
        )
        if digest[field_name] != expected_value
    ]
    if mismatches:
        raise MultiRunPlanError(
            "sample_ref metadata does not match manifest: " + ", ".join(mismatches)
        )


def _single_run_result_to_runner_result(
    request: SingleRunRequest,
    result: Any,
) -> SingleRunRunnerResult:
    run_state_payload = _read_optional_json_mapping(
        Path(str(getattr(result, "run_state_path", "")))
    )
    timing = _single_run_timing_from_state(run_state_payload)
    warnings = _single_run_warnings(run_state_payload)
    errors = _single_run_errors(run_state_payload)
    cleanup_status = _normalize_cleanup_status(
        str(getattr(result, "cleanup_status", ""))
    )
    final_status = str(getattr(result, "final_status", "unknown"))
    failure_kind = _failure_kind_from_single_run(final_status, cleanup_status)
    duration_seconds = _optional_duration_seconds(timing.get("total_seconds"))
    return SingleRunRunnerResult(
        run_id=str(getattr(result, "run_id", request.run_id)),
        case_id=str(getattr(result, "case_id", request.case_id)),
        final_status=final_status,
        case_status=_case_status_from_single_run(final_status, cleanup_status),
        single_run_status=_single_run_status_from_final(final_status),
        cleanup_status=cleanup_status,
        evidence_status=_evidence_status_from_single_run(result, run_state_payload),
        summary_status=_summary_status_from_single_run(result, run_state_payload),
        readiness_status=_readiness_status_from_single_run(run_state_payload),
        verdict=str(getattr(result, "verdict", "") or "unknown"),
        confidence=str(getattr(result, "confidence", "")),
        failure_kind=failure_kind,
        result_source="single_run_runner",
        simulated=request.dry_run,
        error_summary=_first_text(errors),
        evidence_bundle_path=_single_run_result_path(
            request, getattr(result, "evidence_bundle_path", "")
        ),
        run_state_path=_single_run_result_path(
            request, getattr(result, "run_state_path", "")
        ),
        case_summary_path=_single_run_result_path(
            request, getattr(result, "summary_path", "")
        ),
        duration_seconds=duration_seconds,
        timing=timing,
        warnings=warnings,
        unsafe_to_continue=cleanup_status == "restore_failed",
        manual_intervention_required=cleanup_status == "restore_failed",
    )


def _single_run_result_path(request: SingleRunRequest, value: Any) -> str:
    if not value:
        return ""
    batch_root = _batch_root_for_case_dir(request.case_dir)
    return _relative_batch_path(batch_root, str(value))


def _batch_root_for_case_dir(case_dir: Path) -> Path:
    try:
        resolved_case_dir = case_dir.resolve()
    except OSError:
        resolved_case_dir = case_dir.absolute()
    for candidate in (resolved_case_dir, *resolved_case_dir.parents):
        if candidate.name == "cases":
            return candidate.parent
    return resolved_case_dir.parent


def _read_optional_json_mapping(path: Path) -> Mapping[str, Any]:
    if not str(path) or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _single_run_timing_from_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return {}

    step_timings: list[dict[str, Any]] = []
    stage_totals: dict[str, float] = {}
    for item in steps:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        duration = _duration_between_utc_strings(
            str(item.get("started_at_utc", "")),
            str(item.get("finished_at_utc", "")),
        )
        if duration is None:
            continue
        rounded_duration = _round_duration_seconds(duration)
        status = str(item.get("status", ""))
        step_timings.append(
            {
                "name": name,
                "status": status,
                "duration_seconds": rounded_duration,
            }
        )
        stage_totals[name] = stage_totals.get(name, 0.0) + duration

    total = _duration_between_utc_strings(
        str(payload.get("created_at_utc", "")),
        str(payload.get("updated_at_utc", "")),
    )
    if total is None and step_timings:
        total = sum(float(item["duration_seconds"]) for item in step_timings)
    if total is None:
        return {}

    return {
        "schema_version": "multi-run-case-timing.v1",
        "total_seconds": _round_duration_seconds(total),
        "stages": {
            name: _round_duration_seconds(duration)
            for name, duration in sorted(stage_totals.items())
        },
        "steps": step_timings,
    }


def _duration_between_utc_strings(started_at: str, finished_at: str) -> float | None:
    start = _parse_utc_datetime(started_at)
    finish = _parse_utc_datetime(finished_at)
    if start is None or finish is None:
        return None
    duration = (finish - start).total_seconds()
    if duration < 0:
        return None
    return duration


def _parse_utc_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _optional_duration_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_duration_seconds(value: float) -> float:
    return round(max(0.0, value), 6)


def _single_run_warnings(payload: Mapping[str, Any]) -> tuple[str, ...]:
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        return ()
    messages: list[str] = []
    for item in warnings:
        if isinstance(item, Mapping):
            message = str(item.get("message", ""))
        else:
            message = str(item)
        if message:
            messages.append(_redact_multi_run_message(message))
    return tuple(messages)


def _single_run_errors(payload: Mapping[str, Any]) -> tuple[str, ...]:
    messages: list[str] = []
    for key in ("fatal_errors", "errors"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, Mapping):
                message = str(item.get("message", ""))
            else:
                message = str(item)
            if message:
                messages.append(_redact_multi_run_message(message))
    return tuple(messages)


def _normalize_cleanup_status(value: str) -> CleanupStatus:
    normalized = value or "unknown"
    if normalized == "dry_run":
        return "skipped"
    if normalized in CLEANUP_STATUSES:
        return normalized  # type: ignore[return-value]
    return "unknown"


def _case_status_from_single_run(
    final_status: str,
    cleanup_status: CleanupStatus,
) -> CaseStatus:
    if cleanup_status == "restore_failed":
        return "stopped_environment_failure"
    if final_status.startswith("completed"):
        return "completed"
    if final_status.startswith("failed"):
        return "failed"
    return "failed"


def _single_run_status_from_final(final_status: str) -> SingleRunStatus:
    if final_status.startswith("completed"):
        return "completed"
    if final_status == "timeout":
        return "timeout"
    if final_status.startswith("failed"):
        return "failed"
    return "unknown"


def _failure_kind_from_single_run(
    final_status: str,
    cleanup_status: CleanupStatus,
) -> FailureKind | None:
    if cleanup_status in {"restore_failed", "unknown"}:
        return "environment_failure"
    if final_status.startswith("failed") or final_status == "timeout":
        return "case_failure"
    return None


def _evidence_status_from_single_run(
    result: Any,
    payload: Mapping[str, Any],
) -> EvidenceStatus:
    status = str(payload.get("evidence_export_status", ""))
    if status == "saved":
        return "exported"
    if status in {"failed", "skipped"}:
        return status  # type: ignore[return-value]
    path = getattr(result, "evidence_bundle_path", None)
    return "exported" if path else "not_started"


def _summary_status_from_single_run(
    result: Any,
    payload: Mapping[str, Any],
) -> SummaryStatus:
    stages = payload.get("stages")
    if isinstance(stages, Mapping):
        summary = stages.get("summary")
        if isinstance(summary, Mapping) and summary.get("path"):
            return "collected"
    path = getattr(result, "summary_path", None)
    return "collected" if path else "missing"


def _readiness_status_from_single_run(payload: Mapping[str, Any]) -> ReadinessStatus:
    readiness = payload.get("security_product_readiness")
    if isinstance(readiness, Mapping):
        status = str(readiness.get("status", ""))
        if status in READINESS_STATUSES:
            return status  # type: ignore[return-value]
    stages = payload.get("stages")
    if isinstance(stages, Mapping):
        stage = stages.get("security_product_readiness")
        if isinstance(stage, Mapping):
            status = str(stage.get("status", ""))
            if status in READINESS_STATUSES:
                return status  # type: ignore[return-value]
    return "unknown"


def _first_text(values: Iterable[str]) -> str:
    for value in values:
        if value:
            return value
    return ""


def _safe_multi_run_message(error: BaseException) -> str:
    return _redact_multi_run_message(f"{type(error).__name__}: {error}")


def _redact_multi_run_message(message: str) -> str:
    return re.sub(
        r"(?i)\b(authorization|bearer|token|secret|password|credential|"
        r"api[_-]?key|cloud[_-]?secret)\b(\s*[:=]\s*)?[^\s,;\"']*",
        r"\1=<redacted>",
        message,
    )[:500]


def execute_multi_run_batch(
    batch_dir: Path | str,
    *,
    runner: SingleRunRunner | None = None,
    execution_mode: MultiRunExecutionMode = "run",
    preflight_checker: MultiRunPreflightChecker | None = None,
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
    if execution_mode == "run":
        state = _initial_multi_run_state(
            plan,
            manifest=manifest,
            batch_plan_sha256=batch_plan_sha256,
        )
    else:
        state = load_multi_run_state(state_path)
        _ensure_existing_batch_can_execute(
            state,
            plan=plan,
            manifest=manifest,
            batch_plan_sha256=batch_plan_sha256,
            execution_mode=execution_mode,
        )
    state = replace(state, batch_state="running", final_status="")
    write_multi_run_state(state_path, state)
    active_runner = runner or FakeSingleRunRunner()
    append_next_multi_run_event(
        event_log_path,
        batch_id=plan.batch_id,
        event_type="preflight_started",
        data={"selected_indexes": selected_indexes},
    )
    preflight_report = run_multi_run_preflight(
        root,
        plan=plan,
        manifest=manifest,
        batch_plan_sha256=batch_plan_sha256,
        runner=active_runner,
        checker=preflight_checker,
    )
    preflight_report_path = root / "preflight_report.json"
    _write_json_atomic(preflight_report_path, preflight_report.to_dict())
    preflight_failed_messages = [
        f"{check.name}: {check.message}" for check in preflight_report.failed_checks
    ]
    append_next_multi_run_event(
        event_log_path,
        batch_id=plan.batch_id,
        event_type="preflight_passed"
        if preflight_report.passed
        else "preflight_failed",
        data={
            "manifest_sha256": manifest.sha256,
            "batch_plan_sha256": batch_plan_sha256,
            "runner": "fake" if runner is None else type(runner).__name__,
            "preflight_report_path": preflight_report_path.name,
            "failed_checks": preflight_failed_messages,
        },
    )
    if not preflight_report.passed:
        state = replace(
            state,
            batch_state="failed_preflight",
            final_status="failed_preflight",
            batch_cleanup_status="not_required",
            emergency_poweroff_status="not_needed",
            errors=tuple(preflight_failed_messages),
        )
        write_multi_run_state(state_path, state)
        append_next_multi_run_event(
            event_log_path,
            batch_id=plan.batch_id,
            event_type="batch_finished",
            data={
                "final_status": state.final_status,
                "batch_state": state.batch_state,
                "batch_cleanup_status": state.batch_cleanup_status,
                "emergency_poweroff_status": state.emergency_poweroff_status,
                "fastmode_enabled": state.fastmode_enabled,
                "preflight_report_path": preflight_report_path.name,
            },
        )
        return state

    entries_by_index = manifest.by_index()
    cases_by_index = {case.sample_index: case for case in state.cases}
    stop_state: BatchState | None = None
    errors: list[str] = []
    warnings: list[str] = []
    unsafe_to_continue = False
    manual_intervention_required = False
    manual_intervention_reason = ""
    reuse_environment_from_case_id = ""
    run_indexes = tuple(
        index
        for index in selected_indexes
        if _should_run_case_for_mode(cases_by_index[index], execution_mode)
    )

    for index in selected_indexes:
        entry = entries_by_index[index]
        planned_case = cases_by_index[index]
        if not _should_run_case_for_mode(planned_case, execution_mode):
            append_next_multi_run_event(
                event_log_path,
                batch_id=plan.batch_id,
                event_type="case_skipped_by_execution_mode",
                sample_index=index,
                sample_id=entry.sample_id,
                run_id=planned_case.run_id,
                case_id=planned_case.case_id,
                case_status=planned_case.case_status,
                data={
                    "execution_mode": execution_mode,
                    "resume_eligible": planned_case.resume_eligible,
                    "failure_kind": planned_case.failure_kind,
                },
            )
            continue
        attempt = planned_case.attempt + 1
        case_dir = _case_attempt_dir(
            root / "cases" / _case_dir_name(index, entry.case_name),
            attempt,
        )
        case_dir.mkdir(parents=True, exist_ok=True)
        request = _single_run_request_for_entry(
            plan,
            entry=entry,
            case_id=planned_case.case_id,
            case_dir=case_dir,
            batch_plan_sha256=batch_plan_sha256,
            attempt=attempt,
            defer_final_cleanup=_should_defer_case_cleanup(
                plan,
                run_indexes=run_indexes,
                current_index=index,
            ),
            skip_initial_restore=bool(reuse_environment_from_case_id),
            environment_reused_from_case_id=reuse_environment_from_case_id,
        )
        reuse_environment_from_case_id = ""
        append_next_multi_run_event(
            event_log_path,
            batch_id=plan.batch_id,
            event_type="case_started",
            sample_index=index,
            sample_id=entry.sample_id,
            run_id=request.run_id,
            case_id=request.case_id,
            case_status="planned",
            data={
                "defer_final_cleanup": request.defer_final_cleanup,
                "skip_initial_restore": request.skip_initial_restore,
                "environment_reused_from_case_id": (
                    request.environment_reused_from_case_id
                ),
            },
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
        runner_started = time.monotonic()
        result = active_runner.run(request)
        result = _runner_result_with_scheduler_timing(
            result,
            elapsed_seconds=time.monotonic() - runner_started,
        )
        failure_kind = classify_runner_result(result)
        case_state = result.to_case_state(request)
        if plan.execution.fastmode:
            fastmode_eligible, fastmode_reason = _fastmode_gate_decision(root, result)
            case_state = replace(
                case_state,
                fastmode_eligible=fastmode_eligible,
                fastmode_reason=fastmode_reason,
            )
            if (
                fastmode_eligible
                and index != run_indexes[-1]
                and not result.unsafe_to_continue
                and not result.manual_intervention_required
            ):
                reuse_environment_from_case_id = result.case_id
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
                "failure_kind": failure_kind,
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
        burn_state, burn_warning = _burn_indexed_sample_after_persisted_result(
            root,
            request=request,
            result=result,
        )
        if burn_state is not None:
            case_warnings = case_state.warnings
            if burn_warning:
                warnings.append(burn_warning)
                case_warnings = (*case_warnings, burn_warning)
            case_state = replace(
                case_state,
                indexed_sample_state=burn_state,
                warnings=case_warnings,
            )
            cases_by_index[index] = case_state
            state = replace(
                state,
                cases=_cases_in_selected_order(cases_by_index, selected_indexes),
                warnings=tuple(warnings),
            )
            write_multi_run_state(state_path, state)
            append_next_multi_run_event(
                event_log_path,
                batch_id=plan.batch_id,
                event_type="indexed_sample_burned"
                if burn_state == "burned"
                else "indexed_sample_burn_failed",
                sample_index=index,
                sample_id=entry.sample_id,
                run_id=result.run_id,
                case_id=result.case_id,
                case_status=result.case_status,
                data={
                    "indexed_sample_state": burn_state,
                    "sample_ref": _relative_batch_path(root, request.sample_ref),
                    "warning": burn_warning,
                },
            )

        stop_state = _stop_state_for_result(result, plan)
        if stop_state is not None:
            state = replace(
                state,
                batch_state=stop_state,
                final_status=stop_state,
                finished_at_utc=utc_now(),
            )
            state = _with_terminal_batch_cleanup_state(state)
            write_multi_run_state(state_path, state)
            break

    if stop_state is None:
        state = replace(
            state,
            batch_state=_completed_batch_state(state),
            final_status=_completed_batch_state(state),
            finished_at_utc=utc_now(),
        )
        state = _with_terminal_batch_cleanup_state(state)
        write_multi_run_state(state_path, state)

    aggregate = write_multi_run_aggregate_summary(root, state)
    append_next_multi_run_event(
        event_log_path,
        batch_id=plan.batch_id,
        event_type="aggregate_summary_written",
        data={
            "aggregate_summary_path": aggregate.summary_json_path.name,
            "aggregate_summary_markdown_path": aggregate.summary_markdown_path.name,
        },
    )
    append_next_multi_run_event(
        event_log_path,
        batch_id=plan.batch_id,
        event_type="batch_finished",
        data={
            "batch_state": state.batch_state,
            "final_status": state.final_status,
            "batch_cleanup_status": state.batch_cleanup_status,
            "emergency_poweroff_status": state.emergency_poweroff_status,
            "fastmode_enabled": state.fastmode_enabled,
            "unsafe_to_continue": state.unsafe_to_continue,
            "manual_intervention_required": state.manual_intervention_required,
        },
    )
    return state


def write_multi_run_aggregate_summary(
    batch_dir: Path | str,
    state: MultiRunState,
) -> MultiRunAggregateArtifacts:
    root = Path(batch_dir)
    summary = build_multi_run_aggregate_summary(root, state)
    summary_json_path = root / "aggregate_summary.json"
    summary_markdown_path = root / "aggregate_summary.md"
    _write_json_atomic(summary_json_path, summary)
    summary_markdown_path.write_text(
        render_multi_run_aggregate_markdown(summary),
        encoding="utf-8",
    )
    return MultiRunAggregateArtifacts(
        summary_json_path=summary_json_path,
        summary_markdown_path=summary_markdown_path,
        summary=summary,
    )


def build_multi_run_aggregate_summary(
    batch_dir: Path | str,
    state: MultiRunState,
) -> dict[str, Any]:
    root = Path(batch_dir)
    cases = list(state.cases)
    selected_samples = len(state.selected_indexes)
    case_failures = [case for case in cases if case.failure_kind == "case_failure"]
    environment_failures = [
        case for case in cases if case.failure_kind == "environment_failure"
    ]
    completed_cases = [case for case in cases if case.case_status == "completed"]
    evaluable_cases = [
        case
        for case in completed_cases
        if case.failure_kind is None and case.verdict != "not_evaluable"
    ]
    detected_cases = [
        case for case in evaluable_cases if case.verdict == "detected_or_blocked"
    ]
    not_evaluable_cases = [
        case
        for case in cases
        if case.verdict == "not_evaluable" or case.failure_kind is not None
    ]
    denominator = {
        "selected_samples": selected_samples,
        "planned_cases": len(cases),
        "completed_cases": len(completed_cases),
        "evaluable_cases": len(evaluable_cases),
        "not_evaluable_cases": len(not_evaluable_cases),
        "case_failures": len(case_failures),
        "environment_failures": len(environment_failures),
        "environment_stopped": bool(
            environment_failures
            or state.batch_state == "stopped_for_environment_failure"
            or state.unsafe_to_continue
        ),
    }
    detection_rate = {
        "detected_or_blocked": len(detected_cases),
        "denominator": len(evaluable_cases),
        "rate": (
            round(len(detected_cases) / len(evaluable_cases), 6)
            if evaluable_cases
            else None
        ),
        "simulated": any(case.simulated for case in cases),
        "rate_kind": (
            "simulated_detection_rate"
            if any(case.simulated for case in cases)
            else "observed_detection_rate"
        ),
    }
    fastmode_metrics = _build_fastmode_metrics(state, cases)
    if state.fastmode_enabled:
        detection_rate["rate_kind"] = "fastmode_observed_detection_rate"
        detection_rate["experimental"] = True
        detection_rate["baseline_comparable"] = False
    return {
        "schema_version": MULTI_RUN_AGGREGATE_SUMMARY_SCHEMA_VERSION,
        "batch_id": state.batch_id,
        "generated_at_utc": utc_now(),
        "product_id": state.product_id,
        "instance_id": state.instance_id,
        "snapshot_id": state.snapshot_id,
        "region": state.region,
        "batch_state": state.batch_state,
        "final_status": state.final_status,
        "batch_cleanup_status": state.batch_cleanup_status,
        "emergency_poweroff_status": state.emergency_poweroff_status,
        "fastmode_enabled": state.fastmode_enabled,
        "fastmode": fastmode_metrics,
        "runtime_parameters": _build_runtime_parameters(root, state, cases),
        "manifest_sha256": state.manifest_sha256,
        "batch_plan_sha256": state.batch_plan_sha256,
        "selected_indexes": list(state.selected_indexes),
        "denominator": denominator,
        "detection_rate": detection_rate,
        "verdict_breakdown": _count_case_field(cases, "verdict"),
        "readiness_breakdown": _count_case_field(cases, "readiness_status"),
        "status_breakdown": {
            "case_status": _count_case_field(cases, "case_status"),
            "cleanup_status": _count_case_field(cases, "cleanup_status"),
            "indexed_sample_state": _count_case_field(
                cases,
                "indexed_sample_state",
            ),
            "evidence_status": _count_case_field(cases, "evidence_status"),
            "summary_status": _count_case_field(cases, "summary_status"),
        },
        "timing": _build_multi_run_timing_summary(cases),
        "case_errors": _aggregate_case_errors(cases),
        "cases": [_aggregate_case_payload(root, case) for case in cases],
        "paths": {
            "state": "multi_run_state.json",
            "events": "multi_run_events.jsonl",
            "aggregate_summary": "aggregate_summary.json",
            "aggregate_summary_markdown": "aggregate_summary.md",
        },
    }


def render_multi_run_aggregate_markdown(summary: Mapping[str, Any]) -> str:
    denominator = _mapping_or_empty(summary.get("denominator"))
    detection_rate = _mapping_or_empty(summary.get("detection_rate"))
    fastmode = _mapping_or_empty(summary.get("fastmode"))
    runtime_parameters = _mapping_or_empty(summary.get("runtime_parameters"))
    timing = _mapping_or_empty(summary.get("timing"))
    stages = _mapping_or_empty(timing.get("stages"))
    case_errors = summary.get("case_errors")
    error_count = len(case_errors) if isinstance(case_errors, list) else 0
    lines = [
        "# Multi-Run Aggregate Summary",
        "",
        f"- Batch: {summary.get('batch_id', '')}",
        f"- Product: {summary.get('product_id', '')}",
        f"- Final status: {summary.get('final_status', '')}",
        f"- Batch cleanup: {summary.get('batch_cleanup_status', '')}",
        f"- Emergency poweroff: {summary.get('emergency_poweroff_status', '')}",
        f"- Fastmode enabled: {summary.get('fastmode_enabled', False)}",
        f"- Selected samples: {denominator.get('selected_samples', 0)}",
        f"- Evaluable cases: {denominator.get('evaluable_cases', 0)}",
        f"- Case failures: {denominator.get('case_failures', 0)}",
        f"- Environment failures: {denominator.get('environment_failures', 0)}",
        (
            "- Detection rate: "
            f"{detection_rate.get('detected_or_blocked', 0)}/"
            f"{detection_rate.get('denominator', 0)}"
        ),
        f"- Case errors: {error_count}",
        "",
    ]
    if summary.get("fastmode_enabled", False):
        lines.extend(
            [
                "## Fastmode",
                "",
                "- Mode: experimental; results are not comparable with clean snapshot baseline runs.",
                f"- Eligible cases: {fastmode.get('eligible_cases', 0)}",
                f"- Environment reuse cases: {fastmode.get('used_cases', 0)}",
                f"- Deferred cleanup cases: {fastmode.get('deferred_cleanup_cases', 0)}",
                f"- Detection metric kind: {detection_rate.get('rate_kind', '')}",
                "",
            ]
        )
    if runtime_parameters:
        lines.extend(
            [
                "## Runtime Parameters",
                "",
                f"- fastmode: {_enabled_label(runtime_parameters.get('fastmode'))}",
                f"- cleanup strategy: {runtime_parameters.get('cleanup_strategy', '')}",
                "- effective cleanup strategy: "
                f"{runtime_parameters.get('effective_cleanup_strategy', '')}",
                "- settling cooldown seconds: "
                f"{runtime_parameters.get('settling_cooldown_seconds', '')}",
                "- upload status timeout seconds: "
                f"{runtime_parameters.get('upload_status_timeout_seconds', '')}",
                "- post-execution default delay seconds: "
                f"{runtime_parameters.get('post_execution_collection_delay_seconds', '')}",
                "- product probe: "
                f"{_enabled_label(runtime_parameters.get('product_probe_enabled'))}",
                "- execution-stage probe: "
                f"{_enabled_label(runtime_parameters.get('execution_product_probe_enabled'))}",
                "- post-execution probe interval seconds: "
                f"{runtime_parameters.get('post_execution_probe_interval_seconds', '')}",
                "- execution probe interval seconds: "
                f"{runtime_parameters.get('execution_product_probe_interval_seconds', '')}",
                "- quarantine short delay seconds: "
                f"{runtime_parameters.get('post_execution_quarantine_delay_seconds', '')}",
                "",
            ]
        )
    lines.extend(["## Verdict Breakdown", ""])
    verdict_breakdown = _mapping_or_empty(summary.get("verdict_breakdown"))
    if verdict_breakdown:
        lines.extend(
            f"- {verdict}: {count}"
            for verdict, count in sorted(verdict_breakdown.items())
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Timing", ""])
    if timing:
        lines.append(f"- Average case seconds: {timing.get('average_case_seconds', 0)}")
        lines.append(f"- P95 case seconds: {timing.get('p95_case_seconds', 0)}")
        lines.append(f"- Timed cases: {timing.get('timed_cases', 0)}")
        if stages:
            lines.extend(["", "### Stage Timing", ""])
            for stage_name, stage_payload in sorted(stages.items()):
                if isinstance(stage_payload, Mapping):
                    lines.append(
                        "- "
                        f"{stage_name}: avg={stage_payload.get('average_seconds', 0)}s "
                        f"p95={stage_payload.get('p95_seconds', 0)}s "
                        f"count={stage_payload.get('count', 0)}"
                    )
    else:
        lines.append("- none")
    lines.extend(["", "## Case Errors", ""])
    if isinstance(case_errors, list) and case_errors:
        for item in case_errors:
            if isinstance(item, dict):
                lines.append(
                    "- "
                    f"{item.get('case_id', '')}: "
                    f"{item.get('failure_kind', '')} "
                    f"{item.get('error_summary', '')}".rstrip()
                )
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


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
        fastmode=bool(execution_payload.get("fastmode", False)),
        case_timeout_seconds=_optional_float(
            execution_payload.get("case_timeout_seconds"), plan_path
        ),
        settling_cooldown_seconds=_optional_float_with_default(
            execution_payload.get("settling_cooldown_seconds"),
            plan_path,
            DEFAULT_MULTI_RUN_SETTLING_COOLDOWN_SECONDS,
        ),
        upload_status_timeout_seconds=_optional_float_with_default(
            execution_payload.get("upload_status_timeout_seconds"),
            plan_path,
            DEFAULT_MULTI_RUN_UPLOAD_STATUS_TIMEOUT_SECONDS,
        ),
        post_execution_collection_delay_seconds=_optional_float_with_default(
            execution_payload.get("post_execution_collection_delay_seconds"),
            plan_path,
            DEFAULT_MULTI_RUN_POST_EXECUTION_COLLECTION_DELAY_SECONDS,
        ),
        product_probe_enabled=bool(
            execution_payload.get("product_probe_enabled", False)
        ),
        post_execution_probe_interval_seconds=_optional_float_with_default(
            execution_payload.get("post_execution_probe_interval_seconds"),
            plan_path,
            DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS,
        ),
        product_probe_available=bool(
            execution_payload.get("product_probe_available", False)
        ),
        product_probe_skip_reason=str(
            execution_payload.get("product_probe_skip_reason", "")
        ),
        execution_product_probe_enabled=bool(
            execution_payload.get("execution_product_probe_enabled", False)
        ),
        execution_product_probe_interval_seconds=_optional_float_with_default(
            execution_payload.get("execution_product_probe_interval_seconds"),
            plan_path,
            DEFAULT_MULTI_RUN_POST_EXECUTION_PROBE_INTERVAL_SECONDS,
        ),
        post_execution_quarantine_delay_seconds=_optional_float_with_default(
            execution_payload.get("post_execution_quarantine_delay_seconds"),
            plan_path,
            DEFAULT_MULTI_RUN_POST_EXECUTION_QUARANTINE_DELAY_SECONDS,
        ),
        environment_failure_policy=str(
            execution_payload.get("environment_failure_policy", "stop")
        ),
        cleanup_strategy=_cleanup_strategy_from_json(
            execution_payload.get("cleanup_strategy"), plan_path
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
        guest_agent_url=str(payload.get("guest_agent_url", "")),
        desktop_worker_url=str(payload.get("desktop_worker_url", "")),
        single_run_runner_version=str(payload.get("single_run_runner_version", "")),
        multi_run_version=str(payload.get("multi_run_version", "")),
        product_profile_version=str(payload.get("product_profile_version", "")),
    )


def load_existing_multi_run_batch(
    *,
    batch_root: Path | str,
    batch_id: str,
    manifest: LoadedSampleManifest,
    selection: BatchSelection,
    product_id: str,
    instance_id: str,
    snapshot_id: str,
    region: str,
) -> MultiRunPlanArtifacts:
    resolved_batch_id = _safe_batch_id(batch_id)
    batch_dir = Path(batch_root) / resolved_batch_id
    batch_plan_path = batch_dir / "batch_plan.json"
    state_path = batch_dir / "multi_run_state.json"
    plan = load_batch_plan(batch_plan_path)
    batch_plan_sha256 = compute_bytes_sha256(batch_plan_path.read_bytes())
    state = load_multi_run_state(state_path)
    _ensure_existing_batch_matches_request(
        plan,
        state=state,
        manifest=manifest,
        selection=selection,
        product_id=product_id,
        instance_id=instance_id,
        snapshot_id=snapshot_id,
        region=region,
        batch_plan_sha256=batch_plan_sha256,
    )
    return MultiRunPlanArtifacts(
        batch_dir=batch_dir,
        batch_plan_path=batch_plan_path,
        generated_config_path=batch_dir / "multi_run.generated.toml",
        manifest_copy_path=batch_dir / plan.sample_manifest_path,
        manifest_sha256_path=batch_dir / "sample_manifest.sha256",
        state_path=state_path,
        event_log_path=batch_dir / "multi_run_events.jsonl",
        batch_plan_sha256=batch_plan_sha256,
        batch_plan=plan,
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


def _optional_float_with_default(value: Any, path: Path, default: float) -> float:
    parsed = _optional_float(value, path)
    return default if parsed is None else parsed


def _cleanup_strategy_from_json(value: Any, path: Path) -> CleanupStrategy:
    if value is None:
        return "per_case"
    if not isinstance(value, str) or value not in CLEANUP_STRATEGIES:
        allowed = ", ".join(CLEANUP_STRATEGIES)
        raise MultiRunPlanError(
            f"{path}: execution.cleanup_strategy must be one of: {allowed}"
        )
    return value  # type: ignore[return-value]


def _single_run_request_for_entry(
    plan: BatchPlan,
    *,
    entry: SampleManifestEntry,
    case_id: str,
    case_dir: Path,
    batch_plan_sha256: str,
    attempt: int = 1,
    defer_final_cleanup: bool = False,
    skip_initial_restore: bool = False,
    environment_reused_from_case_id: str = "",
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
        guest_agent_url=plan.guest_agent_url,
        desktop_worker_url=plan.desktop_worker_url,
        sample_ref=entry.sample_ref,
        manifest_sha256=plan.manifest_sha256,
        batch_plan_sha256=batch_plan_sha256,
        sha256=entry.sha256,
        md5=entry.md5,
        size=entry.size,
        original_filename=entry.original_filename,
        case_dir=case_dir,
        dry_run=plan.execution.dry_run,
        attempt=attempt,
        defer_final_cleanup=defer_final_cleanup,
        skip_initial_restore=skip_initial_restore,
        environment_reused_from_case_id=environment_reused_from_case_id,
        settling_cooldown_seconds=plan.execution.settling_cooldown_seconds,
        upload_status_timeout_seconds=plan.execution.upload_status_timeout_seconds,
        post_execution_collection_delay_seconds=(
            plan.execution.post_execution_collection_delay_seconds
        ),
        product_probe_enabled=plan.execution.product_probe_enabled,
        post_execution_probe_interval_seconds=(
            plan.execution.post_execution_probe_interval_seconds
        ),
        product_probe_available=plan.execution.product_probe_available,
        product_probe_skip_reason=plan.execution.product_probe_skip_reason,
        execution_product_probe_enabled=(
            plan.execution.execution_product_probe_enabled
        ),
        execution_product_probe_interval_seconds=(
            plan.execution.execution_product_probe_interval_seconds
        ),
        post_execution_quarantine_delay_seconds=(
            plan.execution.post_execution_quarantine_delay_seconds
        ),
    )


def _case_dir_name(sample_index: int, case_name: str) -> str:
    return f"{sample_index:04d}_{_safe_batch_id(case_name)[:16]}"


def _case_attempt_dir(case_dir: Path, attempt: int) -> Path:
    if attempt <= 1:
        return case_dir
    return case_dir / "attempts" / f"attempt_{attempt:03d}"


def _cases_in_selected_order(
    cases_by_index: Mapping[int, CaseState],
    selected_indexes: tuple[int, ...],
) -> tuple[CaseState, ...]:
    return tuple(cases_by_index[index] for index in selected_indexes)


def classify_runner_result(result: SingleRunRunnerResult) -> FailureKind | None:
    if result.failure_kind is not None:
        return result.failure_kind
    if result.unsafe_to_continue or result.manual_intervention_required:
        return "environment_failure"
    if result.cleanup_status in {"restore_failed", "unknown"}:
        return "environment_failure"
    if result.case_status == "failed" or result.single_run_status in {
        "failed",
        "timeout",
    }:
        return "case_failure"
    if result.summary_status == "missing":
        return "case_failure"
    return None


def _case_resume_eligible(result: SingleRunRunnerResult) -> bool:
    return (
        not result.simulated
        and classify_runner_result(result) is None
        and result.case_status == "completed"
        and result.single_run_status == "completed"
        and result.cleanup_status == "restored"
    )


def _check_batch_directory_writable(batch_dir: Path) -> MultiRunPreflightCheck:
    test_path = batch_dir / ".preflight-write-test"
    try:
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink(missing_ok=True)
    except OSError as exc:
        return _preflight_failed(
            "batch_directory_writable",
            f"batch directory is not writable: {type(exc).__name__}",
        )
    return _preflight_passed("batch_directory_writable", "batch directory is writable")


def _check_manifest_digest(
    plan: BatchPlan,
    manifest: LoadedSampleManifest,
) -> MultiRunPreflightCheck:
    if manifest.sha256 != plan.manifest_sha256:
        return _preflight_failed(
            "manifest_digest",
            "sample manifest sha256 does not match batch plan",
            {
                "manifest_sha256": manifest.sha256,
                "plan_manifest_sha256": plan.manifest_sha256,
            },
        )
    return _preflight_passed(
        "manifest_digest",
        "sample manifest digest is present and matches batch plan",
        {"manifest_sha256": manifest.sha256},
    )


def _check_selected_indexes(
    plan: BatchPlan,
    manifest: LoadedSampleManifest,
) -> MultiRunPreflightCheck:
    available = set(manifest.indexes)
    missing = [
        index for index in plan.selection.selected_indexes if index not in available
    ]
    if missing:
        return _preflight_failed(
            "selected_indexes_valid",
            "batch plan selected unavailable sample indexes",
            {"missing_indexes": missing},
        )
    if not plan.selection.selected_indexes:
        return _preflight_failed("selected_indexes_valid", "no samples selected")
    return _preflight_passed(
        "selected_indexes_valid",
        "selected sample indexes are available in manifest",
        {"selected_indexes": plan.selection.selected_indexes},
    )


def _check_runner_callable(runner: SingleRunRunner) -> MultiRunPreflightCheck:
    if not callable(getattr(runner, "run", None)):
        return _preflight_failed("runner_callable", "single-run runner is not callable")
    return _preflight_passed("runner_callable", "single-run runner is callable")


def _check_product_profile(product_id: str) -> MultiRunPreflightCheck:
    from .single_run import supported_single_run_products

    supported = supported_single_run_products()
    if product_id not in supported:
        return _preflight_failed(
            "product_profile_known",
            f"unsupported security product: {product_id}",
            {"supported_products": supported},
        )
    return _preflight_passed(
        "product_profile_known",
        "security product profile is available",
        {"product_id": product_id},
    )


def _check_cloud_identifier(name: str, value: str) -> MultiRunPreflightCheck:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}", value):
        return _preflight_failed(
            f"{name}_format",
            f"{name} format is invalid",
            {"value_present": bool(value)},
        )
    return _preflight_passed(f"{name}_format", f"{name} format is valid")


def _check_region(region: str) -> MultiRunPreflightCheck:
    if not re.fullmatch(r"[a-z]+(?:-[a-z0-9]+)+", region):
        return _preflight_failed(
            "region_format",
            "region format is invalid",
            {"value_present": bool(region)},
        )
    return _preflight_passed("region_format", "region format is valid")


def _check_http_url(name: str, url: str) -> MultiRunPreflightCheck:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return _preflight_failed(
            f"{name}_format",
            f"{name} must be an http or https URL with a hostname",
            {"value_present": bool(url)},
        )
    return _preflight_passed(
        f"{name}_format",
        f"{name} has a valid URL shape",
        {"scheme": parsed.scheme, "host_configured": True},
    )


def _safe_preflight_call(
    name: str,
    callback: Any,
) -> MultiRunPreflightCheck:
    try:
        check = callback()
    except Exception as exc:  # noqa: BLE001 - convert checker issues into report data.
        return _preflight_failed(
            name, f"preflight checker failed: {type(exc).__name__}"
        )
    if not isinstance(check, MultiRunPreflightCheck):
        return _preflight_failed(name, "preflight checker returned invalid result")
    return check


def _check_instance_lock_available(
    batch_dir: Path,
    instance_id: str,
    batch_id: str,
) -> MultiRunPreflightCheck:
    busy_batches: list[str] = []
    for state_path in batch_dir.parent.glob("*/multi_run_state.json"):
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("batch_id") == batch_id:
            continue
        if payload.get("instance_id") != instance_id:
            continue
        if payload.get("batch_state") in {"running", "stopping"}:
            busy_batches.append(str(payload.get("batch_id") or state_path.parent.name))
    if busy_batches:
        return _preflight_failed(
            "instance_lock_available",
            "another running batch uses the same instance",
            {"busy_batches": busy_batches},
        )
    return _preflight_passed(
        "instance_lock_available",
        "no running sibling batch uses this instance",
    )


def _check_evidence_output_writable(batch_dir: Path) -> MultiRunPreflightCheck:
    evidence_probe_dir = batch_dir / "cases"
    try:
        evidence_probe_dir.mkdir(parents=True, exist_ok=True)
        probe_path = evidence_probe_dir / ".preflight-evidence-write-test"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
    except OSError as exc:
        return _preflight_failed(
            "evidence_output_writable",
            f"evidence output directory is not writable: {type(exc).__name__}",
        )
    return _preflight_passed(
        "evidence_output_writable",
        "evidence output directory is writable",
    )


def _check_generated_config_has_no_secrets(path: Path) -> MultiRunPreflightCheck:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _preflight_failed(
            "generated_config_no_secrets",
            f"generated config could not be read: {type(exc).__name__}",
        )
    sensitive_markers = (
        "token",
        "secret",
        "password",
        "credential",
        "authorization",
        "bearer",
        "api_key",
        "cloud_secret",
        "tencentcloud_secret",
    )
    lowered = text.casefold()
    matched = [marker for marker in sensitive_markers if marker in lowered]
    if matched:
        return _preflight_failed(
            "generated_config_no_secrets",
            "generated config contains sensitive-looking keys",
            {"matched_markers": matched},
        )
    return _preflight_passed(
        "generated_config_no_secrets",
        "generated config contains no sensitive-looking keys",
    )


def _check_batch_plan_sha256(batch_plan_sha256: str) -> MultiRunPreflightCheck:
    if len(batch_plan_sha256) != 64 or any(
        ch not in "0123456789abcdef" for ch in batch_plan_sha256
    ):
        return _preflight_failed("batch_plan_sha256", "batch plan sha256 is invalid")
    return _preflight_passed("batch_plan_sha256", "batch plan sha256 is present")


def _preflight_passed(
    name: str,
    message: str,
    data: Mapping[str, Any] | None = None,
) -> MultiRunPreflightCheck:
    return MultiRunPreflightCheck(
        name=name,
        status="passed",
        message=message,
        data=data or {},
    )


def _preflight_failed(
    name: str,
    message: str,
    data: Mapping[str, Any] | None = None,
) -> MultiRunPreflightCheck:
    return MultiRunPreflightCheck(
        name=name,
        status="failed",
        message=message,
        data=data or {},
    )


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


def _ensure_existing_batch_matches_request(
    plan: BatchPlan,
    *,
    state: MultiRunState,
    manifest: LoadedSampleManifest,
    selection: BatchSelection,
    product_id: str,
    instance_id: str,
    snapshot_id: str,
    region: str,
    batch_plan_sha256: str,
) -> None:
    if manifest.sha256 != plan.manifest_sha256:
        raise MultiRunStateError("manifest sha256 does not match existing batch plan")
    if state.batch_plan_sha256 != batch_plan_sha256:
        raise MultiRunStateError("batch plan sha256 does not match multi_run_state")
    if state.selected_indexes != plan.selection.selected_indexes:
        raise MultiRunStateError("multi_run_state selected indexes do not match plan")
    state_values = {
        "product_id": state.product_id,
        "instance_id": state.instance_id,
        "snapshot_id": state.snapshot_id,
        "region": state.region,
        "manifest_sha256": state.manifest_sha256,
    }
    plan_values = {
        "product_id": plan.product_id,
        "instance_id": plan.instance_id,
        "snapshot_id": plan.snapshot_id,
        "region": plan.region,
        "manifest_sha256": plan.manifest_sha256,
    }
    state_mismatches = [
        field_name
        for field_name, state_value in state_values.items()
        if state_value != plan_values[field_name]
    ]
    if state_mismatches:
        formatted = ", ".join(state_mismatches)
        raise MultiRunStateError(f"multi_run_state metadata mismatch: {formatted}")
    expected = {
        "product_id": product_id,
        "instance_id": instance_id,
        "snapshot_id": snapshot_id,
        "region": region,
    }
    actual = {
        "product_id": plan.product_id,
        "instance_id": plan.instance_id,
        "snapshot_id": plan.snapshot_id,
        "region": plan.region,
    }
    mismatches = [
        field_name
        for field_name, expected_value in expected.items()
        if expected_value != actual[field_name]
    ]
    if mismatches:
        formatted = ", ".join(mismatches)
        raise MultiRunStateError(f"existing batch metadata mismatch: {formatted}")
    if selection.selected_indexes != plan.selection.selected_indexes:
        raise MultiRunStateError("selected indexes do not match existing batch plan")


def _ensure_existing_batch_can_execute(
    state: MultiRunState,
    *,
    plan: BatchPlan,
    manifest: LoadedSampleManifest,
    batch_plan_sha256: str,
    execution_mode: MultiRunExecutionMode,
) -> None:
    if execution_mode == "run":
        return
    if state.unsafe_to_continue or state.manual_intervention_required:
        raise MultiRunStateError(
            "existing batch is unsafe to continue; manual intervention required"
        )
    if state.batch_plan_sha256 != batch_plan_sha256:
        raise MultiRunStateError("batch plan sha256 does not match multi_run_state")
    _ensure_existing_batch_matches_request(
        plan,
        state=state,
        manifest=manifest,
        selection=plan.selection,
        product_id=plan.product_id,
        instance_id=plan.instance_id,
        snapshot_id=plan.snapshot_id,
        region=plan.region,
        batch_plan_sha256=batch_plan_sha256,
    )
    _ensure_no_burned_indexed_sample_will_run(state, execution_mode)


def _ensure_no_burned_indexed_sample_will_run(
    state: MultiRunState,
    execution_mode: MultiRunExecutionMode,
) -> None:
    blocked = [
        case
        for case in state.cases
        if case.indexed_sample_state == "burned"
        and _should_run_case_for_mode(case, execution_mode)
    ]
    if not blocked:
        return
    formatted = ", ".join(f"{case.sample_index}:{case.case_id}" for case in blocked[:5])
    raise MultiRunStateError(
        "cannot rerun burned indexed samples; regenerate the batch plan "
        f"or run an explicit re-index flow first: {formatted}"
    )


def _should_run_case_for_mode(
    case: CaseState,
    execution_mode: MultiRunExecutionMode,
) -> bool:
    if execution_mode in {"run", "force_rerun"}:
        return True
    if execution_mode == "resume":
        return not case.resume_eligible
    if execution_mode == "rerun_failed":
        if case.failure_kind == "environment_failure":
            return False
        return case.failure_kind == "case_failure" or case.case_status == "failed"
    raise MultiRunPlanError(f"unsupported multi-run execution mode: {execution_mode}")


def _stop_state_for_result(
    result: SingleRunRunnerResult,
    plan: BatchPlan,
) -> BatchState | None:
    failure_kind = classify_runner_result(result)
    if failure_kind == "environment_failure":
        return "stopped_for_environment_failure"
    if (
        failure_kind == "case_failure"
        and plan.execution.failure_policy == "stop-on-case-failure"
    ):
        return "stopped_for_case_failure"
    return None


def _should_defer_case_cleanup(
    plan: BatchPlan,
    *,
    run_indexes: tuple[int, ...],
    current_index: int,
) -> bool:
    if plan.execution.cleanup_strategy != "deferred" and not plan.execution.fastmode:
        return False
    if not run_indexes or current_index == run_indexes[-1]:
        return False
    return True


def _fastmode_gate_decision(
    root: Path,
    result: SingleRunRunnerResult,
) -> tuple[bool, str]:
    if result.unsafe_to_continue or result.manual_intervention_required:
        return False, "unsafe_to_continue"
    if classify_runner_result(result) is not None:
        return False, "case_or_environment_failure"
    if result.verdict != "detected_or_blocked":
        return False, f"verdict={result.verdict or 'unknown'}"
    if result.confidence != "high":
        return False, f"confidence={result.confidence or 'unknown'}"

    summary_path = root / result.case_summary_path if result.case_summary_path else None
    summary = _read_optional_json_mapping(summary_path) if summary_path else {}
    if not summary:
        return False, "case_summary_missing"
    if not _summary_contains_strong_attribution(summary):
        return False, "strong_attribution_missing"
    return True, "evaluator_detected_or_blocked_high_confidence_strong_attribution"


def _summary_contains_strong_attribution(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).casefold()
            if key_text in {"attribution", "attribution_level"}:
                if isinstance(item, Mapping):
                    if str(item.get("level", "")).casefold() == "strong":
                        return True
                elif str(item).casefold() == "strong":
                    return True
            if _summary_contains_strong_attribution(item):
                return True
    if isinstance(value, list | tuple):
        return any(_summary_contains_strong_attribution(item) for item in value)
    return False


def _with_terminal_batch_cleanup_state(state: MultiRunState) -> MultiRunState:
    cleanup_status, emergency_status = _terminal_batch_cleanup_status(state)
    return replace(
        state,
        batch_cleanup_status=cleanup_status,
        emergency_poweroff_status=emergency_status,
    )


def _terminal_batch_cleanup_status(
    state: MultiRunState,
) -> tuple[BatchCleanupStatus, EmergencyPoweroffStatus]:
    if state.batch_state == "failed_preflight":
        return "not_required", "not_needed"
    if (
        state.unsafe_to_continue
        or state.manual_intervention_required
        or state.batch_state == "stopped_for_environment_failure"
        or any(case.cleanup_status == "restore_failed" for case in state.cases)
    ):
        return "restore_failed", "attempted"
    if any(
        case.cleanup_status in {"restored", "deferred_to_next_case"}
        for case in state.cases
    ):
        return "restored", "not_needed"
    return "not_required", "not_needed"


def _burn_indexed_sample_after_persisted_result(
    root: Path,
    *,
    request: SingleRunRequest,
    result: SingleRunRunnerResult,
) -> tuple[IndexedSampleState | None, str]:
    if not _is_indexed_sample_burn_eligible(result):
        return None, ""

    sample_path = Path(request.sample_ref)
    indexed_dir = root / "sample_index" / "indexed"
    if not _is_path_relative_to(sample_path, indexed_dir):
        return None, ""
    if not sample_path.is_file():
        return (
            "burn_failed",
            "indexed sample mirror was not found when burn-after-use ran",
        )

    try:
        sample_path.unlink()
        sample_path.write_bytes(b"")
    except OSError as exc:
        return (
            "burn_failed",
            _redact_multi_run_message(
                f"failed to burn indexed sample mirror: {type(exc).__name__}: {exc}"
            ),
        )

    return "burned", ""


def _is_indexed_sample_burn_eligible(result: SingleRunRunnerResult) -> bool:
    terminal_case = result.case_status in {
        "completed",
        "failed",
        "skipped",
        "stopped_environment_failure",
    }
    return (
        result.indexed_sample_state == "available"
        and terminal_case
        and result.summary_status == "collected"
        and result.evidence_status == "exported"
        and not result.unsafe_to_continue
        and not result.manual_intervention_required
    )


def _is_path_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


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


def _count_case_field(cases: Iterable[CaseState], field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        value = getattr(case, field_name)
        key = str(value) if value not in (None, "") else "none"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _build_fastmode_metrics(
    state: MultiRunState,
    cases: Iterable[CaseState],
) -> dict[str, Any]:
    case_list = list(cases)
    eligible_cases = [case for case in case_list if case.fastmode_eligible]
    used_cases = [case for case in case_list if case.fastmode_used]
    deferred_cleanup_cases = [
        case for case in case_list if case.cleanup_status == "deferred_to_next_case"
    ]
    return {
        "enabled": state.fastmode_enabled,
        "experimental": state.fastmode_enabled,
        "baseline_comparable": not state.fastmode_enabled,
        "eligible_cases": len(eligible_cases),
        "used_cases": len(used_cases),
        "deferred_cleanup_cases": len(deferred_cleanup_cases),
        "eligible_case_ids": [case.case_id for case in eligible_cases],
        "environment_reuse_case_ids": [case.case_id for case in used_cases],
        "accuracy_warning": (
            "fastmode reuses environment between selected cases; metrics are "
            "exploratory and must not be compared with clean snapshot baseline runs"
            if state.fastmode_enabled
            else ""
        ),
    }


def _build_runtime_parameters(
    root: Path,
    state: MultiRunState,
    cases: Iterable[CaseState],
) -> dict[str, Any]:
    execution = _load_batch_execution_payload(root)
    deferred_cleanup_seen = any(
        case.cleanup_status == "deferred_to_next_case" for case in cases
    )
    cleanup_strategy = str(execution.get("cleanup_strategy", ""))
    return {
        "fastmode": state.fastmode_enabled,
        "cleanup_strategy": cleanup_strategy,
        "effective_cleanup_strategy": (
            "deferred_between_cases" if deferred_cleanup_seen else cleanup_strategy
        ),
        "settling_cooldown_seconds": _runtime_float(
            execution, "settling_cooldown_seconds"
        ),
        "upload_status_timeout_seconds": _runtime_float(
            execution, "upload_status_timeout_seconds"
        ),
        "post_execution_collection_delay_seconds": _runtime_float(
            execution, "post_execution_collection_delay_seconds"
        ),
        "product_probe_enabled": _runtime_bool(execution, "product_probe_enabled"),
        "post_execution_probe_interval_seconds": _runtime_float(
            execution, "post_execution_probe_interval_seconds"
        ),
        "execution_product_probe_enabled": _runtime_bool(
            execution, "execution_product_probe_enabled"
        ),
        "execution_product_probe_interval_seconds": _runtime_float(
            execution, "execution_product_probe_interval_seconds"
        ),
        "post_execution_quarantine_delay_seconds": _runtime_float(
            execution, "post_execution_quarantine_delay_seconds"
        ),
    }


def _load_batch_execution_payload(root: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads((root / "batch_plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    execution = payload.get("execution")
    return execution if isinstance(execution, Mapping) else {}


def _runtime_float(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _runtime_bool(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    return bool(value) if isinstance(value, bool) else False


def _enabled_label(value: Any) -> str:
    return "enabled" if bool(value) else "disabled"


def _build_multi_run_timing_summary(cases: Iterable[CaseState]) -> dict[str, Any]:
    case_list = list(cases)
    case_durations = [
        float(case.duration_seconds)
        for case in case_list
        if case.duration_seconds is not None
    ]
    stage_values: dict[str, list[float]] = {}
    scheduler_durations: list[float] = []
    for case in case_list:
        timing = case.timing
        scheduler_duration = _optional_duration_seconds(
            timing.get("scheduler_duration_seconds")
        )
        if scheduler_duration is not None:
            scheduler_durations.append(scheduler_duration)
        stages = timing.get("stages")
        if not isinstance(stages, Mapping):
            continue
        for stage_name, value in stages.items():
            duration = _optional_duration_seconds(value)
            if duration is None:
                continue
            stage_values.setdefault(str(stage_name), []).append(duration)
    if scheduler_durations:
        stage_values.setdefault("scheduler", []).extend(scheduler_durations)

    duration_stats = _duration_stats(case_durations)
    return {
        "schema_version": "multi-run-timing-summary.v1",
        "timed_cases": duration_stats["count"],
        "average_case_seconds": duration_stats["average_seconds"],
        "p95_case_seconds": duration_stats["p95_seconds"],
        "case_duration": duration_stats,
        "stages": {
            name: _duration_stats(values)
            for name, values in sorted(stage_values.items())
        },
        "slowest_cases": _slowest_cases(case_list),
    }


def _duration_stats(values: Iterable[float]) -> dict[str, Any]:
    durations = sorted(_round_duration_seconds(value) for value in values)
    count = len(durations)
    total = _round_duration_seconds(sum(durations))
    return {
        "count": count,
        "total_seconds": total,
        "average_seconds": _round_duration_seconds(total / count) if count else 0,
        "p95_seconds": _percentile(durations, 0.95) if durations else 0,
    }


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = int(round((len(sorted_values) - 1) * percentile))
    index = max(0, min(index, len(sorted_values) - 1))
    return sorted_values[index]


def _slowest_cases(
    cases: Iterable[CaseState], *, limit: int = 5
) -> list[dict[str, Any]]:
    timed_cases = [case for case in cases if case.duration_seconds is not None]
    timed_cases.sort(
        key=lambda case: float(case.duration_seconds or 0),
        reverse=True,
    )
    return [
        {
            "sample_index": case.sample_index,
            "sample_id": case.sample_id,
            "case_id": case.case_id,
            "duration_seconds": _round_duration_seconds(
                float(case.duration_seconds or 0)
            ),
        }
        for case in timed_cases[:limit]
    ]


def _aggregate_case_payload(root: Path, case: CaseState) -> dict[str, Any]:
    return {
        "sample_index": case.sample_index,
        "sample_id": case.sample_id,
        "case_name": case.case_name,
        "case_id": case.case_id,
        "run_id": case.run_id,
        "case_status": case.case_status,
        "single_run_status": case.single_run_status,
        "cleanup_status": case.cleanup_status,
        "indexed_sample_state": case.indexed_sample_state,
        "evidence_status": case.evidence_status,
        "summary_status": case.summary_status,
        "readiness_status": case.readiness_status,
        "verdict": case.verdict,
        "confidence": case.confidence,
        "failure_kind": case.failure_kind,
        "result_source": case.result_source,
        "simulated": case.simulated,
        "resume_eligible": case.resume_eligible,
        "duration_seconds": case.duration_seconds,
        "timing": _jsonable(case.timing),
        "fastmode_eligible": case.fastmode_eligible,
        "fastmode_reason": case.fastmode_reason,
        "fastmode_used": case.fastmode_used,
        "environment_reused_from_case_id": case.environment_reused_from_case_id,
        "error_summary": case.error_summary,
        "warnings": list(case.warnings),
        "paths": {
            "case_summary": _relative_batch_path(root, case.case_summary_path),
            "run_state": _relative_batch_path(root, case.run_state_path),
            "evidence_bundle": _relative_batch_path(root, case.evidence_bundle_path),
        },
    }


def _aggregate_case_errors(cases: Iterable[CaseState]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for case in cases:
        if not case.error_summary and case.failure_kind is None:
            continue
        errors.append(
            {
                "sample_index": case.sample_index,
                "sample_id": case.sample_id,
                "case_name": case.case_name,
                "case_id": case.case_id,
                "case_status": case.case_status,
                "failure_kind": case.failure_kind,
                "cleanup_status": case.cleanup_status,
                "summary_status": case.summary_status,
                "error_summary": case.error_summary,
            }
        )
    return errors


def _relative_batch_path(root: Path, value: str) -> str:
    if not value:
        return ""
    path = Path(value)
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


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
            indexed_sample_state=_initial_indexed_sample_state(entries_by_index[index]),
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
        fastmode_enabled=plan.execution.fastmode,
    )


def _planned_case_id(sample_index: int, sample_id: str, product_id: str) -> str:
    safe_sample = _safe_batch_id(sample_id)
    safe_product = _safe_batch_id(product_id).casefold()
    return f"{sample_index:04d}_{safe_sample}__{safe_product}"


def _initial_indexed_sample_state(entry: SampleManifestEntry) -> IndexedSampleState:
    if entry.sample_source_kind == "local_platform_path":
        return "available"
    return "not_required"


def write_multi_run_state(path: Path | str, state: MultiRunState) -> None:
    _write_json_atomic(Path(path), state.to_dict())


def load_multi_run_state(path: Path | str) -> MultiRunState:
    state_path = Path(path)
    payload = read_multi_run_state_payload(state_path)
    cases_payload = payload.get("cases")
    if not isinstance(cases_payload, list):
        raise MultiRunStateError(f"{state_path}: cases must be a list")
    return MultiRunState(
        batch_id=_state_str(payload, "batch_id", state_path),
        batch_state=_state_str(payload, "batch_state", state_path),
        product_id=_state_str(payload, "product_id", state_path),
        instance_id=_state_str(payload, "instance_id", state_path),
        snapshot_id=_state_str(payload, "snapshot_id", state_path),
        region=_state_str(payload, "region", state_path),
        sample_manifest_path=_state_str(payload, "sample_manifest_path", state_path),
        manifest_sha256=_state_str(payload, "manifest_sha256", state_path),
        batch_plan_sha256=_state_str(payload, "batch_plan_sha256", state_path),
        selected_indexes=tuple(
            _state_positive_int(value, "selected_indexes", state_path)
            for value in _state_list(payload, "selected_indexes", state_path)
        ),
        cases=tuple(
            _case_state_from_payload(item, state_path, index)
            for index, item in enumerate(cases_payload, start=1)
        ),
        unsafe_to_continue=bool(payload.get("unsafe_to_continue", False)),
        manual_intervention_required=bool(
            payload.get("manual_intervention_required", False)
        ),
        manual_intervention_reason=str(payload.get("manual_intervention_reason", "")),
        started_at_utc=str(payload.get("started_at_utc", "")),
        finished_at_utc=str(payload.get("finished_at_utc", "")),
        final_status=str(payload.get("final_status", "")),
        batch_cleanup_status=_batch_cleanup_status_from_payload(
            payload.get("batch_cleanup_status")
        ),
        emergency_poweroff_status=_emergency_poweroff_status_from_payload(
            payload.get("emergency_poweroff_status")
        ),
        fastmode_enabled=bool(payload.get("fastmode_enabled", False)),
        errors=tuple(str(item) for item in payload.get("errors", [])),
        warnings=tuple(str(item) for item in payload.get("warnings", [])),
    )


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


def _case_state_from_payload(
    payload: Any,
    state_path: Path,
    case_number: int,
) -> CaseState:
    if not isinstance(payload, dict):
        raise MultiRunStateError(
            f"{state_path}: cases[{case_number}] must be an object"
        )
    return CaseState(
        sample_index=_state_positive_int(
            payload.get("sample_index"), "cases.sample_index", state_path
        ),
        sample_id=_state_str(payload, "sample_id", state_path),
        case_name=_state_str(payload, "case_name", state_path),
        case_id=_state_str(payload, "case_id", state_path),
        run_id=str(payload.get("run_id", "")),
        attempt=int(payload.get("attempt", 0)),
        case_status=str(payload.get("case_status", "planned")),
        single_run_status=str(payload.get("single_run_status", "not_started")),
        cleanup_status=str(payload.get("cleanup_status", "not_started")),
        indexed_sample_state=_indexed_sample_state_from_payload(
            payload.get("indexed_sample_state")
        ),
        evidence_status=str(payload.get("evidence_status", "not_started")),
        summary_status=str(payload.get("summary_status", "not_started")),
        readiness_status=str(payload.get("readiness_status", "unknown")),
        resume_eligible=bool(payload.get("resume_eligible", False)),
        verdict=str(payload.get("verdict", "unknown")),
        confidence=str(payload.get("confidence", "")),
        failure_kind=_optional_failure_kind(payload.get("failure_kind"), state_path),
        result_source=str(payload.get("result_source", "")),
        simulated=bool(payload.get("simulated", False)),
        error_summary=str(payload.get("error_summary", "")),
        evidence_bundle_path=str(payload.get("evidence_bundle_path", "")),
        run_state_path=str(payload.get("run_state_path", "")),
        case_summary_path=str(payload.get("case_summary_path", "")),
        duration_seconds=_optional_float(payload.get("duration_seconds"), state_path),
        timing=_state_timing_payload(payload.get("timing")),
        fastmode_eligible=bool(payload.get("fastmode_eligible", False)),
        fastmode_reason=str(payload.get("fastmode_reason", "")),
        fastmode_used=bool(payload.get("fastmode_used", False)),
        environment_reused_from_case_id=str(
            payload.get("environment_reused_from_case_id", "")
        ),
        warnings=tuple(str(item) for item in payload.get("warnings", [])),
    )


def _state_timing_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(value)


def _indexed_sample_state_from_payload(value: Any) -> IndexedSampleState:
    if value in INDEXED_SAMPLE_STATES:
        return value  # type: ignore[return-value]
    return "available"


def _batch_cleanup_status_from_payload(value: Any) -> BatchCleanupStatus:
    if value in BATCH_CLEANUP_STATUSES:
        return value  # type: ignore[return-value]
    return "not_started"


def _emergency_poweroff_status_from_payload(value: Any) -> EmergencyPoweroffStatus:
    if value in EMERGENCY_POWEROFF_STATUSES:
        return value  # type: ignore[return-value]
    return "not_started"


def _state_str(payload: Mapping[str, Any], field_name: str, path: Path) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise MultiRunStateError(f"{path}: {field_name} must be a non-empty string")
    return value


def _state_list(
    payload: Mapping[str, Any],
    field_name: str,
    path: Path,
) -> list[Any]:
    value = payload.get(field_name)
    if not isinstance(value, list):
        raise MultiRunStateError(f"{path}: {field_name} must be a list")
    return value


def _state_positive_int(value: Any, field_name: str, path: Path) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise MultiRunStateError(f"{path}: {field_name} must be a positive integer")
    return value


def _optional_failure_kind(value: Any, path: Path) -> FailureKind | None:
    if value in (None, ""):
        return None
    if value not in FAILURE_KINDS:
        raise MultiRunStateError(f"{path}: invalid failure_kind {value!r}")
    return value


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
