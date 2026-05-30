from __future__ import annotations

import json
import platform
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from cloud_av_agent_lab.guest_agent_server.collectors.tencent_pc_manager import (
    DEFAULT_QQPCMGR_ROOT,
    DEFAULT_QUARANTINE_DIR,
    DEFAULT_TAV_CACHE_PATH,
    PRODUCT_ID,
    RAW_PRODUCT_NAME,
    normalize_tav_md5,
    stat_tav_artifact,
)
from cloud_av_agent_lab.guest_agent_server.workspace.io import _utc_now

from .base import (
    SecurityProductReadinessCheck,
    SecurityProductReadinessContext,
    SecurityProductReadinessResult,
)

READINESS_SCOPE = "quarantine_metadata_observability"
PROTECTION_STATE = "unknown"
READINESS_WARNING = (
    "Tencent PC Manager readiness verifies TAV quarantine metadata observability "
    "only; it does not prove real-time protection is enabled."
)


class TencentPcManagerSecurityProductReadinessProbe:
    product_id = PRODUCT_ID

    def __init__(
        self,
        platform_provider: Callable[[], str] | None = None,
    ) -> None:
        self.platform_provider = platform_provider or platform.system

    def check(
        self,
        context: SecurityProductReadinessContext,
    ) -> SecurityProductReadinessResult:
        platform_name = str(self.platform_provider() or "")
        checks: list[SecurityProductReadinessCheck] = []
        warnings: list[str] = [READINESS_WARNING]
        errors: list[str] = []
        reason_codes: list[str] = []

        if platform_name.casefold() != "windows":
            checks.append(
                SecurityProductReadinessCheck(
                    name="windows_platform_supported",
                    status="unsupported",
                    message="Tencent PC Manager readiness is supported only on Windows",
                    data={"platform": platform_name or "unknown"},
                )
            )
            return _result(
                state="unsupported",
                checks=checks,
                warnings=warnings,
                errors=errors,
                reason_codes=["non_windows_platform"],
            )

        checks.append(
            SecurityProductReadinessCheck(
                name="windows_platform_supported",
                status="ok",
                message="Windows platform detected",
                data={"platform": "Windows"},
            )
        )

        paths = _resolve_product_paths(context.log_dir)
        try:
            root_exists = paths.root.is_dir()
            quarantine_exists = paths.quarantine_dir.is_dir()
            tav_cache_exists = paths.tav_cache_path.is_file()
        except OSError as exc:
            checks.append(
                SecurityProductReadinessCheck(
                    name="tencent_pc_manager_product_paths_accessible",
                    status="failed",
                    message="Tencent PC Manager product metadata paths could not be queried",
                    data={"error": type(exc).__name__},
                )
            )
            return _result(
                state="unknown",
                checks=checks,
                warnings=warnings,
                errors=[f"product path query failed: {type(exc).__name__}"],
                reason_codes=["product_path_query_failed"],
            )

        signals = {
            "qqpcmgr_root_exists": root_exists,
            "quarantine_dir_exists": quarantine_exists,
            "tav_cache_exists": tav_cache_exists,
            "product_process_observed": False,
            "product_service_observed": False,
            "qqpcmgr_root": str(paths.root),
            "quarantine_dir": str(paths.quarantine_dir),
            "tav_cache_path": str(paths.tav_cache_path),
        }
        checks.append(
            SecurityProductReadinessCheck(
                name="tencent_pc_manager_product_presence_signals",
                status="ok" if quarantine_exists else "failed",
                message=(
                    "Tencent PC Manager TAV quarantine metadata path is visible"
                    if quarantine_exists
                    else "Tencent PC Manager TAV quarantine metadata path was not found"
                ),
                data=signals,
            )
        )
        if not quarantine_exists:
            errors.append("Tencent PC Manager TAV quarantine directory was not found")
            return _result(
                state="not_ready",
                checks=checks,
                warnings=warnings,
                errors=errors,
                reason_codes=["quarantine_dir_not_found"],
            )

        sample_md5, sample_warning = _case_sample_md5(context.workspace)
        if sample_warning:
            warnings.append(sample_warning)
            reason_codes.append(sample_warning)

        baseline: dict[str, Any] = {
            "schema_version": "tav-quarantine-readiness-baseline.v1",
            "product_id": PRODUCT_ID,
            "raw_product": RAW_PRODUCT_NAME,
            "sample_md5": sample_md5,
            "quarantine_dir": str(paths.quarantine_dir),
            "tav_cache_path": str(paths.tav_cache_path),
            "product_presence": signals,
            "raw_artifacts_copied": False,
        }
        if sample_md5:
            container = stat_tav_artifact(
                paths.quarantine_dir / sample_md5,
                kind="quarantine_container_baseline",
            )
            icon_sidecar = stat_tav_artifact(
                paths.quarantine_dir / f"{sample_md5}.ico",
                kind="icon_sidecar_baseline",
            )
            baseline["quarantine_container"] = container.to_dict()
            baseline["icon_sidecar"] = icon_sidecar.to_dict()
        else:
            baseline["quarantine_container"] = {}
            baseline["icon_sidecar"] = {}

        tav_cache = stat_tav_artifact(paths.tav_cache_path, kind="tav_cache_baseline")
        baseline["tav_cache"] = tav_cache.to_dict()
        if not tav_cache.present:
            warnings.append("tav_cache_missing")
            reason_codes.append("tav_cache_missing")

        checks.append(
            SecurityProductReadinessCheck(
                name="tav_quarantine_baseline_recorded",
                status="ok",
                message="TAV quarantine metadata baseline was recorded",
                data=baseline,
            )
        )

        if tav_cache.present:
            return _result(
                state="ready",
                checks=checks,
                warnings=warnings,
                errors=errors,
                reason_codes=reason_codes,
            )
        return _result(
            state="partial",
            checks=checks,
            warnings=warnings,
            errors=errors,
            reason_codes=reason_codes,
        )


class _TencentPcManagerPaths:
    def __init__(self, root: Path, quarantine_dir: Path, tav_cache_path: Path) -> None:
        self.root = root
        self.quarantine_dir = quarantine_dir
        self.tav_cache_path = tav_cache_path


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
    tav_cache_path = root / "TAVWfsDB" / "TAVCacheFullEx.db"
    return _TencentPcManagerPaths(
        root=root,
        quarantine_dir=quarantine_dir,
        tav_cache_path=tav_cache_path,
    )


def _case_sample_md5(workspace: Path) -> tuple[str, str]:
    state = _read_json(workspace / "case_state.json")
    md5 = _mapping_value(state, "sample", "md5")
    if not md5:
        case_data = _read_json(workspace / "case.json")
        md5 = _mapping_value(case_data, "sample", "md5")
    if not md5:
        return "", "sample_md5_missing"
    try:
        return normalize_tav_md5(md5), ""
    except ValueError:
        return "", "sample_md5_invalid"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _mapping_value(payload: Mapping[str, Any], table_name: str, key: str) -> str:
    value = payload.get(table_name)
    if isinstance(value, Mapping):
        return str(value.get(key, "")).strip()
    return ""


def _result(
    state: str,
    checks: list[SecurityProductReadinessCheck],
    warnings: list[str],
    errors: list[str],
    reason_codes: list[str],
) -> SecurityProductReadinessResult:
    confidence = "medium" if state in {"ready", "partial"} else "low"
    return SecurityProductReadinessResult(
        product_id=PRODUCT_ID,
        state=state,
        confidence=confidence,
        scope=READINESS_SCOPE,
        protection_state=PROTECTION_STATE,
        checked_at_utc=_utc_now(),
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        checks=tuple(checks),
        warnings=tuple(dict.fromkeys(warnings)),
        errors=tuple(errors),
    )
