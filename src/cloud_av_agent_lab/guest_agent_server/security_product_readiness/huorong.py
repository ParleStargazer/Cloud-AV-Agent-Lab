from __future__ import annotations

import shutil
from pathlib import Path

from cloud_av_agent_lab.guest_agent_server.workspace.io import _utc_now

from .base import (
    SecurityProductReadinessCheck,
    SecurityProductReadinessContext,
    SecurityProductReadinessResult,
)

PRODUCT_ID = "huorong"
LOG_DB = "log.db"
WAL_FILE = "log.db-wal"
SHM_FILE = "log.db-shm"
LOG_FILENAMES = (LOG_DB, SHM_FILE, WAL_FILE)
READINESS_WARNING = (
    "Readiness only verifies Huorong log observability, not real-time protection state."
)


class HuorongSecurityProductReadinessProbe:
    product_id = PRODUCT_ID
    DEFAULT_LOG_DIR = Path(r"C:\ProgramData\Huorong\sysdiag")

    def check(
        self,
        context: SecurityProductReadinessContext,
    ) -> SecurityProductReadinessResult:
        log_dir = Path(context.log_dir) if context.log_dir else self.DEFAULT_LOG_DIR
        snapshot_dir = context.workspace / "security-product-readiness" / PRODUCT_ID
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        checks: list[SecurityProductReadinessCheck] = []
        warnings: list[str] = [READINESS_WARNING]
        errors: list[str] = []

        log_dir_exists = log_dir.is_dir()
        checks.append(
            SecurityProductReadinessCheck(
                name="huorong_log_dir_exists",
                status="ok" if log_dir_exists else "failed",
                message=(
                    "Huorong log directory exists"
                    if log_dir_exists
                    else "Huorong log directory was not found"
                ),
                data={"product_path": r"C:\ProgramData\Huorong\sysdiag"},
            )
        )
        if not log_dir_exists:
            errors.append("Huorong log directory was not found")
            return _result("not_ready", checks, warnings, errors)

        log_db = log_dir / LOG_DB
        db_exists = log_db.is_file()
        checks.append(
            SecurityProductReadinessCheck(
                name="huorong_log_db_exists",
                status="ok" if db_exists else "failed",
                message=(
                    "Huorong log database exists"
                    if db_exists
                    else "Huorong log database was not found"
                ),
                data={"filename": LOG_DB},
            )
        )
        if not db_exists:
            errors.append("Huorong log.db was not found in the source log directory")
            return _result("not_ready", checks, warnings, errors)

        db_copy = snapshot_dir / LOG_DB
        copied = _copy_log_file(log_db, db_copy, LOG_DB, checks, errors)
        if not copied:
            return _result("unknown", checks, warnings, errors)

        try:
            stat = db_copy.stat()
        except OSError as exc:
            checks.append(
                SecurityProductReadinessCheck(
                    name="huorong_log_db_snapshot_stat",
                    status="failed",
                    message="Copied Huorong log database metadata could not be read",
                    data={"filename": LOG_DB, "error": type(exc).__name__},
                )
            )
            errors.append(
                "copied Huorong log.db metadata could not be read: "
                f"{type(exc).__name__}"
            )
            return _result("unknown", checks, warnings, errors)

        checks.append(
            SecurityProductReadinessCheck(
                name="huorong_log_db_snapshot_stat",
                status="ok",
                message="Copied Huorong log database metadata is readable",
                data={
                    "filename": LOG_DB,
                    "size": stat.st_size,
                    "mtime_utc": _format_mtime(stat.st_mtime),
                },
            )
        )

        auxiliary_failed = False
        for filename in (SHM_FILE, WAL_FILE):
            source = log_dir / filename
            if not source.is_file():
                checks.append(
                    SecurityProductReadinessCheck(
                        name=f"huorong_{filename.replace('.', '_').replace('-', '_')}_visible",
                        status="ok",
                        message=f"Optional Huorong {filename} is not visible",
                        data={"filename": filename, "visible": False},
                    )
                )
                continue
            copied_aux = _copy_log_file(
                source,
                snapshot_dir / filename,
                filename,
                checks,
                errors,
                core=False,
            )
            if not copied_aux:
                auxiliary_failed = True

        if auxiliary_failed:
            warnings.append("Optional Huorong WAL/SHM snapshot copy was incomplete")
            return _result("partial", checks, warnings, errors)
        return _result("ready", checks, warnings, errors)


def _copy_log_file(
    source: Path,
    destination: Path,
    filename: str,
    checks: list[SecurityProductReadinessCheck],
    errors: list[str],
    core: bool = True,
) -> bool:
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        checks.append(
            SecurityProductReadinessCheck(
                name=(
                    "huorong_log_db_snapshot"
                    if filename == LOG_DB
                    else f"huorong_{filename.replace('.', '_').replace('-', '_')}_snapshot"
                ),
                status="failed" if core else "warn",
                message=f"Huorong {filename} snapshot copy failed",
                data={"filename": filename, "error": type(exc).__name__},
            )
        )
        errors.append(f"failed to copy Huorong {filename}: {type(exc).__name__}")
        return False

    checks.append(
        SecurityProductReadinessCheck(
            name=(
                "huorong_log_db_snapshot"
                if filename == LOG_DB
                else f"huorong_{filename.replace('.', '_').replace('-', '_')}_snapshot"
            ),
            status="ok",
            message=f"Huorong {filename} snapshot copied",
            data={"filename": filename, "snapshot": str(destination.name)},
        )
    )
    return True


def _result(
    state: str,
    checks: list[SecurityProductReadinessCheck],
    warnings: list[str],
    errors: list[str],
) -> SecurityProductReadinessResult:
    confidence = "medium" if state in {"ready", "partial"} else "low"
    return SecurityProductReadinessResult(
        product_id=PRODUCT_ID,
        state=state,
        confidence=confidence,
        scope="log_observability",
        protection_state="unknown",
        checked_at_utc=_utc_now(),
        checks=tuple(checks),
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def _format_mtime(timestamp: float) -> str:
    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )
