from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .run_state import utc_now


class InstanceLockedError(RuntimeError):
    def __init__(self, lock_path: Path, payload: dict[str, Any]) -> None:
        self.lock_path = lock_path
        self.payload = payload
        super().__init__(
            "instance is locked by another run: "
            f"{lock_path} run_id={payload.get('run_id', '')}"
        )


@dataclass
class InstanceLock:
    path: Path
    payload: dict[str, Any]
    acquired: bool = True

    def heartbeat(self) -> None:
        if not self.acquired:
            return
        self.payload["heartbeat_at_utc"] = utc_now()
        _write_json_atomic(self.path, self.payload)

    def release(self) -> None:
        if self.acquired and self.path.exists():
            self.path.unlink()
        self.acquired = False


def acquire_lock(
    locks_dir: Path,
    *,
    instance_id: str,
    run_id: str,
    case_id: str,
    ttl_seconds: float = 7200.0,
    heartbeat_stale_seconds: float = 900.0,
    force_unlock: bool = False,
    pid: int | None = None,
) -> InstanceLock:
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_path = locks_dir / f"{_safe_lock_name(instance_id)}.lock"
    if lock_path.exists():
        existing = _read_json(lock_path)
        if (
            force_unlock
            or _is_expired(existing)
            or _heartbeat_is_stale(existing, heartbeat_stale_seconds)
        ):
            _archive_lock(lock_path)
        else:
            raise InstanceLockedError(lock_path, existing)

    now = datetime.now(timezone.utc)
    payload = {
        "schema_version": "single-run-lock.v1",
        "instance_id": instance_id,
        "run_id": run_id,
        "case_id": case_id,
        "pid": pid,
        "started_at_utc": now.isoformat().replace("+00:00", "Z"),
        "expires_at_utc": (now + timedelta(seconds=ttl_seconds))
        .isoformat()
        .replace("+00:00", "Z"),
        "heartbeat_at_utc": now.isoformat().replace("+00:00", "Z"),
    }
    _write_json_atomic(lock_path, payload)
    return InstanceLock(lock_path, payload)


def lock_file_for(locks_dir: Path, instance_id: str) -> Path:
    return locks_dir / f"{_safe_lock_name(instance_id)}.lock"


def _safe_lock_name(value: str) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value
    )
    return cleaned.strip("._") or "unknown-instance"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _write_json_atomic(
    path: Path,
    payload: dict[str, Any],
    *,
    attempts: int = 3,
    retry_delay_seconds: float = 0.05,
    sleep: Callable[[float], object] = time.sleep,
) -> None:
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    last_error: OSError | None = None
    for attempt in range(max(1, attempts)):
        try:
            temp_path.replace(path)
            return
        except OSError as exc:
            last_error = exc
            if attempt >= max(1, attempts) - 1:
                break
            sleep(retry_delay_seconds)
    try:
        temp_path.unlink()
    except OSError:
        pass
    if last_error is not None:
        raise last_error


def _is_expired(payload: dict[str, Any]) -> bool:
    expires = _parse_utc(str(payload.get("expires_at_utc", "")))
    return expires is not None and expires <= datetime.now(timezone.utc)


def _heartbeat_is_stale(payload: dict[str, Any], stale_seconds: float) -> bool:
    heartbeat = _parse_utc(str(payload.get("heartbeat_at_utc", "")))
    if heartbeat is None:
        return False
    return heartbeat + timedelta(seconds=stale_seconds) <= datetime.now(timezone.utc)


def _parse_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _archive_lock(path: Path) -> None:
    suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    archive_path = path.with_name(f"{path.name}.stale-{suffix}")
    path.replace(archive_path)
