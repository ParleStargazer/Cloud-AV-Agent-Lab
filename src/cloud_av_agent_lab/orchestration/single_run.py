from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cloud_av_agent_lab.adapters.cloud import CloudProviderError, CloudVmAdapter
from cloud_av_agent_lab.adapters.factory import create_cloud_adapter
from cloud_av_agent_lab.adapters.guest_agent_client import (
    GuestAgentClient,
    GuestAgentError,
    GuestAgentResponse,
)
from cloud_av_agent_lab.config import load_config
from cloud_av_agent_lab.core.contracts import LabConfig, TestCase
from cloud_av_agent_lab.core.execution_modes import resolve_execution_mode
from cloud_av_agent_lab.core.safety import assert_safe_config
from cloud_av_agent_lab.evaluation import render_summary_markdown
from cloud_av_agent_lab.network.client import NetworkClient

from .locks import InstanceLock, acquire_lock
from .logging_context import configure_run_logging, run_log_context
from .run_state import RunState
from .timeout import (
    EVIDENCE_EXPORT_TIMEOUT,
    GUEST_CONTROL_TIMEOUT,
    GUEST_HEALTH_TIMEOUT,
    SALVAGE_TIMEOUT,
    NetworkTimeoutProfile,
)

LOGGER = logging.getLogger("cloud_av_agent_lab.orchestration.single_run")

DEFAULT_GUEST_READY_TIMEOUT_SECONDS = 180.0
DEFAULT_GUEST_READY_INTERVAL_SECONDS = 5.0
DEFAULT_GUEST_READY_SUCCESSES = 2
DEFAULT_SETTLING_COOLDOWN_SECONDS = 15.0
DEFAULT_FASTMODE_REUSE_GUEST_READY_SUCCESSES = 1
DEFAULT_FASTMODE_REUSE_SETTLING_COOLDOWN_SECONDS = 3.0
DEFAULT_UPLOAD_INITIAL_WAIT_SECONDS = 10.0
DEFAULT_UPLOAD_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_UPLOAD_POLL_TIMEOUT_SECONDS = 30.0
DEFAULT_EXECUTION_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_EXECUTION_POLL_TIMEOUT_SECONDS = 60.0
DEFAULT_POST_EXECUTION_COLLECTION_DELAY_SECONDS = 45.0
DEFAULT_POST_EXECUTION_PROBE_INTERVAL_SECONDS = 1.0
DEFAULT_POST_EXECUTION_QUARANTINE_DELAY_SECONDS = 3.0
TERMINAL_EXECUTION_STATES = {
    "exited_cleanly",
    "exited_with_error",
    "launch_failed",
    "terminated_or_disappeared",
}
NONFATAL_REMOTE_EXECUTION_ERROR_MARKERS = {
    "desktop worker is required for real execution but is not ready": (
        "desktop_worker_not_ready"
    ),
    "desktop worker is busy": "worker_busy",
    "execution lease has expired": "lease_expired",
    "execution lease has already been consumed": "lease_reused",
    "execution lease": "lease_invalid",
    "uploaded sample is missing before execution": "sample_missing_before_execution",
    "expected_sha256 does not match": "sha256_mismatch",
    "desktop worker only executes registered .exe files": "unsupported_file_type",
    "execution handler is disabled": "execution_handler_disabled",
    "unsupported_file_type": "unsupported_file_type",
    "handler_id does not match": "execution_handler_mismatch",
    "uploaded sample failed to start": "launch_failed",
    "execute_uploaded_sample requires a previously uploaded sample": "not_uploaded",
}
SENSITIVE_MESSAGE_RE = re.compile(
    r"(?i)\b(authorization|bearer|token|secret|password|credential|api[_-]?key|"
    r"cloud[_-]?secret)\b(\s*[:=]\s*)?[^\s,;\"']*"
)
SINGLE_RUN_PRODUCT_PROFILES: dict[str, dict[str, Any]] = {
    "huorong": {
        "display_name": "Huorong Internet Security",
        "vendor": "Huorong",
        "log_paths": [
            r"C:\ProgramData\Huorong\Sysdiag",
            r"C:\Program Files\Huorong\Sysdiag\log",
        ],
        "ui_window_titles": ["Huorong", "火绒"],
        "detection_keywords": [
            "blocked",
            "quarantine",
            "virus",
            "trojan",
            "malware",
            "拦截",
            "隔离",
        ],
    },
    "windows-defender": {
        "display_name": "Microsoft Defender Antivirus",
        "vendor": "Microsoft",
        "log_paths": [
            "Microsoft-Windows-Windows Defender/Operational",
        ],
        "ui_window_titles": [
            "Windows Security",
            "Microsoft Defender Antivirus",
        ],
        "detection_keywords": [
            "detected",
            "quarantine",
            "blocked",
            "removed",
            "malware",
            "threat",
        ],
    },
    "qihoo-360": {
        "display_name": "360 Total Security",
        "vendor": "Qihoo 360",
        "log_paths": [
            r"C:\ProgramData\360safe\logs",
            r"C:\Program Files (x86)\360\360Safe\logs",
        ],
        "ui_window_titles": ["360 Total Security", "360安全卫士"],
        "detection_keywords": [
            "blocked",
            "quarantine",
            "virus",
            "trojan",
            "malware",
            "拦截",
            "隔离",
        ],
    },
    "tencent-pc-manager": {
        "display_name": "Tencent PC Manager",
        "vendor": "Tencent",
        "log_paths": [
            r"C:\ProgramData\Tencent\QQPCMgr\Quarantine",
            r"C:\ProgramData\Tencent\QQPCMgr\TAVWfsDB\TAVCacheFullEx.db",
        ],
        "ui_window_titles": ["Tencent PC Manager", "QQPCMgr", "腾讯电脑管家"],
        "detection_keywords": [
            "blocked",
            "quarantine",
            "virus",
            "trojan",
            "malware",
            "risk",
            "拦截",
            "隔离",
            "木马",
            "风险",
        ],
    },
}


class SingleRunError(RuntimeError):
    """Raised when single-run orchestration cannot continue."""


@dataclass(frozen=True)
class SingleRunOptions:
    instance_id: str
    snapshot_id: str
    region: str
    sample_name: str
    sample_path: Path
    guest_agent_url: str
    product_id: str = "huorong"
    desktop_worker_url: str = "http://127.0.0.1:8001"
    require_desktop_worker: bool = True
    dry_run: bool = False
    force_unlock: bool = False
    runs_dir: Path = Path("runs")
    guest_ready_timeout_seconds: float = DEFAULT_GUEST_READY_TIMEOUT_SECONDS
    guest_ready_interval_seconds: float = DEFAULT_GUEST_READY_INTERVAL_SECONDS
    guest_ready_successes: int = DEFAULT_GUEST_READY_SUCCESSES
    settling_cooldown_seconds: float = DEFAULT_SETTLING_COOLDOWN_SECONDS
    upload_initial_wait_seconds: float = DEFAULT_UPLOAD_INITIAL_WAIT_SECONDS
    upload_poll_interval_seconds: float = DEFAULT_UPLOAD_POLL_INTERVAL_SECONDS
    upload_poll_timeout_seconds: float = DEFAULT_UPLOAD_POLL_TIMEOUT_SECONDS
    execution_poll_interval_seconds: float = DEFAULT_EXECUTION_POLL_INTERVAL_SECONDS
    execution_poll_timeout_seconds: float = DEFAULT_EXECUTION_POLL_TIMEOUT_SECONDS
    post_execution_collection_delay_seconds: float = (
        DEFAULT_POST_EXECUTION_COLLECTION_DELAY_SECONDS
    )
    product_probe_enabled: bool = False
    post_execution_probe_interval_seconds: float = (
        DEFAULT_POST_EXECUTION_PROBE_INTERVAL_SECONDS
    )
    product_probe_available: bool = False
    product_probe_skip_reason: str = ""
    execution_product_probe_enabled: bool = False
    execution_product_probe_interval_seconds: float = (
        DEFAULT_POST_EXECUTION_PROBE_INTERVAL_SECONDS
    )
    post_execution_quarantine_delay_seconds: float = (
        DEFAULT_POST_EXECUTION_QUARANTINE_DELAY_SECONDS
    )
    cloud_poll_timeout_seconds: float = 600.0
    cloud_poll_interval_seconds: float = 5.0
    lock_ttl_seconds: float = 7200.0
    lock_heartbeat_stale_seconds: float = 900.0
    normal_evidence_timeout: NetworkTimeoutProfile = EVIDENCE_EXPORT_TIMEOUT
    salvage_timeout: NetworkTimeoutProfile = SALVAGE_TIMEOUT
    defer_final_cleanup: bool = False
    skip_initial_restore: bool = False
    fastmode_reuse_guest_ready_successes: int = (
        DEFAULT_FASTMODE_REUSE_GUEST_READY_SUCCESSES
    )
    fastmode_reuse_settling_cooldown_seconds: float = (
        DEFAULT_FASTMODE_REUSE_SETTLING_COOLDOWN_SECONDS
    )


@dataclass(frozen=True)
class ExecutionObservationResult:
    response: GuestAgentResponse
    execution_state: str
    execution_terminal: bool
    root_pid: int | None
    children_count: int
    elapsed_seconds: float
    product_probe_enabled: bool = False
    product_probe_supported: bool = False
    product_probe_count: int = 0
    product_probe_last_state: str = ""
    strong_signal_observed: bool = False
    exit_reason: str = ""

    def to_result_fields(self) -> dict[str, Any]:
        return {
            "execution_terminal": self.execution_terminal,
            "root_pid": self.root_pid,
            "children_count": self.children_count,
            "observation_elapsed_seconds": self.elapsed_seconds,
            "product_probe_enabled": self.product_probe_enabled,
            "product_probe_supported": self.product_probe_supported,
            "product_probe_count": self.product_probe_count,
            "product_probe_last_state": self.product_probe_last_state,
            "strong_signal_observed": self.strong_signal_observed,
            "observation_exit_reason": self.exit_reason,
        }


@dataclass(frozen=True)
class SingleRunResult:
    run_id: str
    case_id: str
    run_dir: Path
    run_state_path: Path
    generated_config_path: Path
    summary_path: Path | None
    evidence_bundle_path: Path | None
    verdict: str
    confidence: str
    final_status: str
    cleanup_status: str
    emergency_poweroff_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "run_dir": str(self.run_dir),
            "run_state_path": str(self.run_state_path),
            "generated_config_path": str(self.generated_config_path),
            "summary_path": str(self.summary_path or ""),
            "evidence_bundle_path": str(self.evidence_bundle_path or ""),
            "verdict": self.verdict,
            "confidence": self.confidence,
            "final_status": self.final_status,
            "cleanup_status": self.cleanup_status,
            "emergency_poweroff_status": self.emergency_poweroff_status,
        }


CloudAdapterFactory = Callable[..., CloudVmAdapter]
GuestClientFactory = Callable[[LabConfig], GuestAgentClient]
SleepFunc = Callable[[float], None]


def run_single_case(
    options: SingleRunOptions,
    *,
    cloud_adapter_factory: CloudAdapterFactory = create_cloud_adapter,
    guest_client_factory: GuestClientFactory | None = None,
    sleep: SleepFunc = time.sleep,
) -> SingleRunResult:
    sample_path = Path(options.sample_path)
    if not sample_path.is_file():
        raise SingleRunError(f"sample file does not exist: {sample_path}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    sample_id = _safe_identifier(options.sample_name)
    product_id = _safe_identifier(options.product_id).casefold()
    case_id = f"{sample_id}__{product_id}__{stamp}"
    run_id = f"{stamp}_{sample_id}__{product_id}"
    run_dir = Path(options.runs_dir) / run_id
    locks_dir = Path(options.runs_dir) / ".locks"
    lock = acquire_lock(
        locks_dir,
        instance_id=options.instance_id,
        run_id=run_id,
        case_id=case_id,
        ttl_seconds=options.lock_ttl_seconds,
        heartbeat_stale_seconds=options.lock_heartbeat_stale_seconds,
        force_unlock=options.force_unlock,
        pid=os.getpid(),
    )

    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        with (
            configure_run_logging(run_dir),
            run_log_context(
                options.instance_id,
                run_id,
            ),
        ):
            return _run_single_case_locked(
                options=options,
                run_id=run_id,
                case_id=case_id,
                sample_id=sample_id,
                product_id=product_id,
                run_dir=run_dir,
                lock=lock,
                cloud_adapter_factory=cloud_adapter_factory,
                guest_client_factory=guest_client_factory,
                sleep=sleep,
            )
    finally:
        lock.release()


def wait_guest_agent_ready(
    client: GuestAgentClient,
    *,
    timeout_seconds: float = DEFAULT_GUEST_READY_TIMEOUT_SECONDS,
    interval_seconds: float = DEFAULT_GUEST_READY_INTERVAL_SECONDS,
    required_successes: int = DEFAULT_GUEST_READY_SUCCESSES,
    timeout_profile: NetworkTimeoutProfile = GUEST_HEALTH_TIMEOUT,
    sleep: SleepFunc = time.sleep,
) -> GuestAgentResponse:
    deadline = time.monotonic() + timeout_seconds
    successes = 0
    last_response: GuestAgentResponse | None = None
    while True:
        elapsed = max(0.0, timeout_seconds - max(deadline - time.monotonic(), 0.0))
        try:
            last_response = client.health(
                timeout_seconds=timeout_profile.socket_timeout_seconds()
            )
        except GuestAgentError as exc:
            if exc.status_code in {401, 403} or exc.source == "local":
                raise
            LOGGER.info(
                "Guest Agent health failed (%.0fs/%.0fs): %s",
                elapsed,
                timeout_seconds,
                type(exc).__name__,
            )
            successes = 0
        else:
            successes += 1
            LOGGER.info("Guest Agent health ok %d/%d", successes, required_successes)
            if successes >= required_successes:
                return last_response

        if time.monotonic() >= deadline:
            raise SingleRunError(
                f"timed out waiting for Guest Agent health after {timeout_seconds:g}s"
            )
        sleep(min(interval_seconds, max(deadline - time.monotonic(), 0.0)))


def wait_desktop_worker_ready(
    client: GuestAgentClient,
    *,
    timeout_seconds: float = DEFAULT_GUEST_READY_TIMEOUT_SECONDS,
    interval_seconds: float = DEFAULT_GUEST_READY_INTERVAL_SECONDS,
    sleep: SleepFunc = time.sleep,
) -> GuestAgentResponse:
    deadline = time.monotonic() + timeout_seconds
    last_reason = ""
    while True:
        elapsed = max(0.0, timeout_seconds - max(deadline - time.monotonic(), 0.0))
        response = client.worker_status()
        ready = bool(response.data.get("desktop_worker_ready", False))
        reason = str(response.data.get("reason", ""))
        last_reason = reason
        if ready:
            LOGGER.info(
                "Desktop Worker ready: session=%s state=%s user=%s",
                response.data.get("worker_session_id"),
                response.data.get("desktop_session_state"),
                response.data.get("username"),
            )
            return response
        LOGGER.info(
            "Desktop Worker not ready (%.0fs/%.0fs): %s",
            elapsed,
            timeout_seconds,
            reason or "not ready",
        )
        if time.monotonic() >= deadline:
            raise SingleRunError(
                "timed out waiting for Desktop Worker ready"
                + (f": {last_reason}" if last_reason else "")
            )
        sleep(min(interval_seconds, max(deadline - time.monotonic(), 0.0)))


def check_guest_agent_ready_once(
    client: GuestAgentClient,
    *,
    timeout_profile: NetworkTimeoutProfile = GUEST_HEALTH_TIMEOUT,
) -> GuestAgentResponse:
    try:
        response = client.health(
            timeout_seconds=timeout_profile.socket_timeout_seconds()
        )
    except GuestAgentError:
        LOGGER.info("Guest Agent quick health failed")
        raise
    LOGGER.info("Guest Agent quick health ok 1/1")
    return response


def check_desktop_worker_ready_once(client: GuestAgentClient) -> GuestAgentResponse:
    response = client.worker_status()
    ready = bool(response.data.get("desktop_worker_ready", False))
    if not ready:
        reason = str(response.data.get("reason", "") or "not ready")
        LOGGER.info("Desktop Worker quick status failed: %s", reason)
        raise SingleRunError(f"Desktop Worker quick status failed: {reason}")
    LOGGER.info(
        "Desktop Worker quick ready: session=%s state=%s user=%s",
        response.data.get("worker_session_id"),
        response.data.get("desktop_session_state"),
        response.data.get("username"),
    )
    return response


def _run_single_case_locked(
    *,
    options: SingleRunOptions,
    run_id: str,
    case_id: str,
    sample_id: str,
    product_id: str,
    run_dir: Path,
    lock: InstanceLock,
    cloud_adapter_factory: CloudAdapterFactory,
    guest_client_factory: GuestClientFactory | None,
    sleep: SleepFunc,
) -> SingleRunResult:
    state = RunState(
        run_dir / "run_state.json",
        run_id=run_id,
        case_id=case_id,
        instance_id=options.instance_id,
        snapshot_id=options.snapshot_id,
        region=options.region,
        product_id=product_id,
        sample_name=sample_id,
        sample_path=str(options.sample_path),
    )
    generated_config_path = run_dir / "lab.generated.toml"
    summary_path: Path | None = None
    evidence_path: Path | None = None
    verdict = ""
    confidence = ""
    case_started = False
    evidence_saved = False
    case_error: BaseException | None = None
    warning_count = 0

    LOGGER.info("single-run started")

    try:
        with state.step("hash_sample"):
            sha256, md5, size = _hash_file(Path(options.sample_path))
            state.set_sample_hash(sha256, size, md5=md5)
            state.mark_stage("delivery", "sample_sha256", sha256)
            state.mark_stage("delivery", "sample_md5", md5)
            state.mark_stage("delivery", "sample_size", size)
            LOGGER.info("sample metadata calculated: name=%s size=%d", sample_id, size)

        with state.step("write_generated_config"):
            generated_config_path.write_text(
                _render_generated_config(
                    options=options,
                    sample_id=sample_id,
                    product_id=product_id,
                    sha256=sha256,
                    md5=md5,
                ),
                encoding="utf-8",
            )
            config = load_config(generated_config_path)
            assert_safe_config(config)
            LOGGER.info("generated non-sensitive config: %s", generated_config_path)

        network = NetworkClient.from_config(config.network)
        adapter = cloud_adapter_factory(
            config,
            network=network,
            dry_run=options.dry_run,
            confirmed_instance_id=("" if options.dry_run else options.instance_id),
            confirmed_snapshot_id=("" if options.dry_run else options.snapshot_id),
            poll_timeout_seconds=options.cloud_poll_timeout_seconds,
            poll_interval_seconds=options.cloud_poll_interval_seconds,
        )
        client = (
            guest_client_factory(config)
            if guest_client_factory is not None
            else GuestAgentClient(config.guest_agent, network=network)
        )
        vm = next(iter(config.vms.values()))
        sample = config.samples[sample_id]
        product = config.products[product_id]
        case = TestCase(id=case_id, sample=sample, vm=vm, product=product)

        if options.skip_initial_restore:
            _mark_initial_restore_skipped(state)
        else:
            _restore_and_start_clean_instance(adapter, vm, state, lock)

        ready_gate_mode = "fastmode_quick" if options.skip_initial_restore else "full"
        guest_ready_successes_required = (
            options.fastmode_reuse_guest_ready_successes
            if options.skip_initial_restore
            else options.guest_ready_successes
        )
        settling_cooldown_seconds = (
            options.fastmode_reuse_settling_cooldown_seconds
            if options.skip_initial_restore
            else options.settling_cooldown_seconds
        )
        state.mark_stage("environment", "ready_gate_mode", ready_gate_mode)
        state.mark_stage(
            "environment",
            "guest_ready_successes_required",
            guest_ready_successes_required,
        )
        state.mark_stage(
            "environment",
            "settling_cooldown_seconds",
            settling_cooldown_seconds,
        )

        with state.step("wait_guest_agent_ready"):
            lock.heartbeat()
            try:
                if options.skip_initial_restore:
                    check_guest_agent_ready_once(client)
                else:
                    wait_guest_agent_ready(
                        client,
                        timeout_seconds=options.guest_ready_timeout_seconds,
                        interval_seconds=options.guest_ready_interval_seconds,
                        required_successes=guest_ready_successes_required,
                        sleep=sleep,
                    )
            except Exception:
                if options.skip_initial_restore:
                    state.mark_stage(
                        "environment",
                        "ready_gate_failure_kind",
                        "quick_gate_failed",
                    )
                    state.mark_stage(
                        "environment",
                        "fallback_restore_attempted",
                        False,
                    )
                raise
            state.mark_stage("environment", "guest_agent_ready", True)

        if config.guest_agent.desktop_worker.enabled:
            with state.step("wait_desktop_worker_ready"):
                lock.heartbeat()
                try:
                    worker_status = (
                        check_desktop_worker_ready_once(client)
                        if options.skip_initial_restore
                        else wait_desktop_worker_ready(
                            client,
                            timeout_seconds=options.guest_ready_timeout_seconds,
                            interval_seconds=options.guest_ready_interval_seconds,
                            sleep=sleep,
                        )
                    )
                except Exception:
                    if options.skip_initial_restore:
                        state.mark_stage(
                            "environment",
                            "ready_gate_failure_kind",
                            "quick_gate_failed",
                        )
                        state.mark_stage(
                            "environment",
                            "fallback_restore_attempted",
                            False,
                        )
                    raise
                state.mark("desktop_worker_ready", True)
                state.mark_stage("environment", "desktop_worker_ready", True)
                state.mark(
                    "desktop_session_state",
                    str(worker_status.data.get("desktop_session_state", "")),
                )
                state.mark_stage(
                    "environment",
                    "desktop_session_state",
                    str(worker_status.data.get("desktop_session_state", "")),
                )
                state.mark(
                    "desktop_worker_session_id",
                    worker_status.data.get("worker_session_id"),
                )
                state.mark_stage(
                    "environment",
                    "desktop_worker_session_id",
                    worker_status.data.get("worker_session_id"),
                )
                state.mark(
                    "desktop_worker_user",
                    str(worker_status.data.get("username", "")),
                )
                state.mark_stage(
                    "environment",
                    "desktop_worker_user",
                    str(worker_status.data.get("username", "")),
                )
                state.mark_stage("environment", "product_readiness_checked", False)
                state.mark_stage("environment", "product_environment_ready", None)
        else:
            state.mark("desktop_worker_ready", False)
            state.mark("desktop_worker_gate", "disabled")
            state.mark_stage("environment", "desktop_worker_ready", False)
            state.mark_stage("environment", "desktop_worker_gate", "disabled")

        with state.step("settling_cooldown"):
            LOGGER.info(
                "Settling cooldown started: %.0fs",
                settling_cooldown_seconds,
            )
            sleep(max(settling_cooldown_seconds, 0.0))
            LOGGER.info("Environment settled; continue prepare-case")

        with state.step("prepare_case"):
            lock.heartbeat()
            client.prepare_case(
                case,
                timeout_seconds=GUEST_CONTROL_TIMEOUT.socket_timeout_seconds(),
            )
            case_started = True
            state.mark("case_started", True)
            state.mark_stage("delivery", "case_started", True)

        with state.step("security_product_readiness"):
            lock.heartbeat()
            readiness_result = _check_security_product_readiness_warning_only(
                client=client,
                state=state,
                case_id=case_id,
                product_id=product_id,
            )
            if readiness_result["status"] == "warning":
                warning_count += 1

        with state.step("upload_sample"):
            lock.heartbeat()
            client.upload_sample(
                case_id=case_id,
                sample_id=sample_id,
                file_path=options.sample_path,
                sha256=sha256,
                md5=md5,
                timeout_seconds=GUEST_CONTROL_TIMEOUT.socket_timeout_seconds(),
            )
            upload_response = _poll_upload_status(
                client,
                case_id,
                initial_wait_seconds=options.upload_initial_wait_seconds,
                poll_interval_seconds=options.upload_poll_interval_seconds,
                timeout_seconds=options.upload_poll_timeout_seconds,
                sleep=sleep,
            )
            upload_state = str(_extract_upload_state(upload_response.data) or "unknown")
            state.mark("post_upload_state", upload_state)
            state.mark_stage("delivery", "upload_state", upload_state)
            state.mark_stage("delivery", "post_upload_state", upload_state)

        with state.step("execute_action"):
            lock.heartbeat()
            execution_result = _execute_after_upload_observation(
                client=client,
                case_id=case_id,
                run_id=run_id,
                sample_id=sample_id,
                expected_sha256=sha256,
                stored_filename=options.sample_path.name,
                upload_state=upload_state,
                dry_run=options.dry_run,
                poll_interval_seconds=options.execution_poll_interval_seconds,
                poll_timeout_seconds=options.execution_poll_timeout_seconds,
                sleep=sleep,
            )
            state.mark("execution_action_status", execution_result["status"])
            state.mark("execution_action_state", execution_result["execution_state"])
            state.mark("execution_action_reason", execution_result["reason"])
            state.mark_stage("execution", "action_status", execution_result["status"])
            state.mark_stage("execution", "state", execution_result["execution_state"])
            state.mark_stage("execution", "reason", execution_result["reason"])
            state.mark_stage(
                "execution",
                "error_source",
                execution_result.get("error_source", "none"),
            )
            state.mark_stage(
                "execution",
                "error_status_code",
                execution_result.get("error_status_code"),
            )
            state.mark_stage("execution", "via", execution_result.get("via", ""))
            state.mark_stage(
                "execution",
                "handler_id",
                execution_result.get("handler_id", ""),
            )
            state.mark_stage(
                "execution",
                "execution_mode",
                execution_result.get("execution_mode", ""),
            )
            state.mark_stage(
                "execution",
                "product_probe_available",
                options.product_probe_available,
            )
            state.mark_stage(
                "execution",
                "product_probe_skip_reason",
                options.product_probe_skip_reason,
            )
            state.mark_stage(
                "execution",
                "execution_product_probe_enabled",
                options.execution_product_probe_enabled,
            )
            state.mark_stage(
                "execution",
                "execution_product_probe_interval_seconds",
                max(options.execution_product_probe_interval_seconds, 0.0),
            )
            state.mark_stage(
                "execution",
                "execution_terminal",
                bool(execution_result.get("execution_terminal", False)),
            )
            state.mark_stage(
                "execution",
                "root_pid",
                execution_result.get("root_pid"),
            )
            state.mark_stage(
                "execution",
                "children_count",
                int(execution_result.get("children_count") or 0),
            )
            state.mark_stage(
                "execution",
                "observation_elapsed_seconds",
                float(execution_result.get("observation_elapsed_seconds") or 0.0),
            )
            state.mark_stage(
                "execution",
                "product_probe_supported",
                bool(execution_result.get("product_probe_supported", False)),
            )
            state.mark_stage(
                "execution",
                "product_probe_count",
                int(execution_result.get("product_probe_count") or 0),
            )
            state.mark_stage(
                "execution",
                "product_probe_last_state",
                str(execution_result.get("product_probe_last_state") or ""),
            )
            state.mark_stage(
                "execution",
                "strong_signal_observed",
                bool(execution_result.get("strong_signal_observed", False)),
            )
            state.mark_stage(
                "execution",
                "observation_exit_reason",
                str(execution_result.get("observation_exit_reason") or ""),
            )
            if execution_result["status"] in {"skipped", "not_started"}:
                state.add_warning("execution", execution_result["reason"])
                warning_count += 1

        with state.step("post_execution_collection_delay"):
            delay_seconds = max(options.post_execution_collection_delay_seconds, 0.0)
            probe_interval_seconds = max(
                options.post_execution_probe_interval_seconds, 0.0
            )
            state.mark_stage(
                "collection",
                "post_execution_collection_delay_seconds",
                delay_seconds,
            )
            state.mark_stage(
                "collection",
                "product_probe_enabled",
                options.product_probe_enabled,
            )
            state.mark_stage(
                "collection",
                "post_execution_probe_interval_seconds",
                probe_interval_seconds,
            )
            state.mark_stage(
                "collection",
                "post_execution_quarantine_delay_seconds",
                max(options.post_execution_quarantine_delay_seconds, 0.0),
            )
            execution_state = execution_result["execution_state"]
            if options.dry_run:
                LOGGER.info(
                    "post-execution collection delay skipped for dry-run "
                    "(configured %.0fs)",
                    delay_seconds,
                )
            elif not _should_wait_after_execution_for_collection(execution_result):
                LOGGER.info(
                    "post-execution collection delay skipped: "
                    "execution_state=%s action_status=%s",
                    execution_state,
                    execution_result["status"],
                )
            elif delay_seconds > 0:
                reason = (
                    "launch failure"
                    if execution_state == "launch_failed"
                    else "execution exit"
                )
                LOGGER.info(
                    "post-execution collection delay started after "
                    "%s: %.0fs to allow security product action "
                    "and log flush",
                    reason,
                    delay_seconds,
                )
                if options.product_probe_enabled and probe_interval_seconds > 0:
                    probe_result = _adaptive_post_execution_collection_delay(
                        client=client,
                        case_id=case_id,
                        product_id=product_id,
                        delay_seconds=delay_seconds,
                        interval_seconds=probe_interval_seconds,
                        sleep=sleep,
                    )
                    for key, value in probe_result.items():
                        state.mark_stage("collection", key, value)
                    if probe_result.get("product_probe_warning"):
                        state.add_warning(
                            "collection",
                            str(probe_result["product_probe_warning"]),
                        )
                        warning_count += 1
                else:
                    sleep(delay_seconds)
                    state.mark_stage(
                        "collection",
                        "product_probe_exit_reason",
                        "disabled_fixed_delay",
                    )
                LOGGER.info(
                    "post-execution collection delay finished; continue log collection"
                )
            else:
                LOGGER.info(
                    "post-execution collection delay disabled; continue log collection"
                )

        with state.step("collect_logs"):
            lock.heartbeat()
            try:
                collection_response = client.collect_logs(
                    case_id,
                    product_id,
                    timeout_seconds=GUEST_CONTROL_TIMEOUT.socket_timeout_seconds(),
                )
            except GuestAgentError as exc:
                if exc.source != "remote" or exc.status_code in {401, 403}:
                    raise
                state.add_warning("collection", str(exc))
                warning_count += 1
                state.mark_stage("collection", "state", "failed")
                state.mark_stage("collection", "error_source", exc.source)
                state.mark_stage("collection", "error_status_code", exc.status_code)
                LOGGER.warning("collection failed nonfatally: %s", exc)
            else:
                state.mark_stage(
                    "collection",
                    "state",
                    str(collection_response.data.get("collection_state", "")),
                )
                state.mark_stage(
                    "collection",
                    "verdict",
                    str(collection_response.data.get("verdict", "")),
                )
                state.mark_stage(
                    "collection",
                    "evidence_count",
                    collection_response.data.get("evidence_count", 0),
                )

        with state.step("case_summary"):
            lock.heartbeat()
            summary_response = client.case_summary(
                case_id,
                timeout_seconds=GUEST_CONTROL_TIMEOUT.socket_timeout_seconds(),
            )
            summary_path = _write_summary_outputs(run_dir, summary_response.data)
            verdict = str(summary_response.data.get("verdict", ""))
            confidence = str(summary_response.data.get("confidence", ""))
            state.mark("test_verdict", verdict)
            state.mark_stage("summary", "verdict", verdict)
            state.mark_stage("summary", "confidence", confidence)
            state.mark_stage("summary", "path", str(summary_path))
            state.mark_artifact("summary_json", str(summary_path))
            state.mark_artifact("summary_markdown", str(run_dir / "case_summary.md"))

        with state.step("export_evidence"):
            lock.heartbeat()
            evidence_response = client.export_evidence_bundle(
                case_id,
                run_dir / f"case_evidence_{case_id}.zip",
                timeout_seconds=options.normal_evidence_timeout.socket_timeout_seconds(),
            )
            evidence_path = Path(str(evidence_response.data.get("output_path", "")))
            evidence_saved = True
            state.mark("evidence_export_status", "saved")
            state.mark("evidence_bundle_path", str(evidence_path))
            state.mark_stage("evidence", "status", "saved")
            state.mark_stage("evidence", "path", str(evidence_path))
            state.mark_stage(
                "evidence",
                "sha256",
                str(evidence_response.data.get("sha256", "")),
            )
            state.mark_stage("evidence", "size", evidence_response.data.get("size"))
            state.mark_artifact("evidence_bundle", str(evidence_path))

    except Exception as exc:
        case_error = exc
        state.add_error("single_run", exc)
        state.add_fatal_error("single_run", exc)
        if isinstance(exc, GuestAgentError) and exc.source == "network":
            state.mark("agent_dead", True)
        LOGGER.error("single-run case flow failed: %s", exc)
    finally:
        _ensure_security_product_readiness_stage_recorded(
            state,
            product_id=product_id,
            case_started=case_started,
        )
        if case_started and not evidence_saved:
            salvaged_path = _try_fast_fail_salvage(
                client=locals().get("client"),
                case_id=case_id,
                run_dir=run_dir,
                state=state,
                timeout=options.salvage_timeout,
            )
            if salvaged_path is not None:
                evidence_path = salvaged_path
                evidence_saved = True
        if _should_defer_final_cleanup(
            options,
            case_error=case_error,
            evidence_saved=evidence_saved,
        ):
            _mark_final_cleanup_deferred(state)
            cleanup_failed = False
        else:
            cleanup_failed = _cleanup_instance(
                adapter=locals().get("adapter"), vm=locals().get("vm"), state=state
            )

    warning_count = max(
        warning_count,
        len(state.data.get("warnings", []))
        if isinstance(state.data.get("warnings"), list)
        else 0,
    )
    final_status = _final_status(case_error, cleanup_failed, warning_count > 0)
    state.mark("status", final_status)
    state.mark("final_status", final_status)
    if case_error is not None and cleanup_failed:
        LOGGER.critical(
            "cleanup restore failed and emergency stop failed. "
            "Manual intervention required: %s",
            options.instance_id,
        )
    LOGGER.info("single-run finished: %s", final_status)
    return SingleRunResult(
        run_id=run_id,
        case_id=case_id,
        run_dir=run_dir,
        run_state_path=run_dir / "run_state.json",
        generated_config_path=generated_config_path,
        summary_path=summary_path,
        evidence_bundle_path=evidence_path,
        verdict=verdict,
        confidence=confidence,
        final_status=final_status,
        cleanup_status=str(state.data.get("cleanup_status", "")),
        emergency_poweroff_status=str(state.data.get("emergency_poweroff_status", "")),
    )


def _restore_and_start_clean_instance(
    adapter: CloudVmAdapter,
    vm: Any,
    state: RunState,
    lock: InstanceLock,
) -> None:
    with state.step("check_instance_status"):
        lock.heartbeat()
        status_response = adapter.get_instance_status(vm)
        LOGGER.info("instance status checked: %s", status_response.status)

    if _instance_state(status_response) == "RUNNING":
        with state.step("stop_before_restore"):
            lock.heartbeat()
            stop_response = adapter.stop_vm(vm)
            LOGGER.info("stop before restore: %s", stop_response.status)

    with state.step("restore_snapshot_initial"):
        lock.heartbeat()
        restore_response = adapter.restore_snapshot(vm)
        LOGGER.info("initial snapshot restore: %s", restore_response.status)

    if _instance_state(restore_response) not in {"RUNNING", ""}:
        with state.step("start_instance"):
            lock.heartbeat()
            start_response = adapter.start_vm(vm)
            LOGGER.info("start instance: %s", start_response.status)


def _mark_initial_restore_skipped(state: RunState) -> None:
    state.mark("initial_restore_status", "skipped_fastmode_reuse")
    state.mark_stage("environment", "initial_restore", "skipped_fastmode_reuse")
    LOGGER.warning("initial restore skipped because fastmode reused environment")


def _cleanup_instance(adapter: object, vm: object, state: RunState) -> bool:
    if adapter is None or vm is None:
        state.mark("cleanup_status", "skipped")
        state.mark("emergency_poweroff_status", "skipped")
        state.mark_stage("cleanup", "status", "skipped")
        state.mark_stage("cleanup", "emergency_poweroff_status", "skipped")
        return False
    try:
        with state.step("cleanup_check_instance_status"):
            status_response = adapter.get_instance_status(vm)  # type: ignore[attr-defined]
            LOGGER.info("cleanup status checked: %s", status_response.status)
        if _instance_state(status_response) == "RUNNING":
            with state.step("cleanup_stop_before_restore"):
                stop_response = adapter.stop_vm(vm)  # type: ignore[attr-defined]
                LOGGER.info("cleanup stop before restore: %s", stop_response.status)
        with state.step("cleanup_restore_snapshot"):
            response = adapter.restore_snapshot(vm)  # type: ignore[attr-defined]
            state.mark("cleanup_status", "dry_run" if response.dry_run else "restored")
            state.mark_stage(
                "cleanup",
                "status",
                "dry_run" if response.dry_run else "restored",
            )
            LOGGER.info("cleanup restore finished: %s", response.status)
        state.mark("emergency_poweroff_status", "not_needed")
        state.mark_stage("cleanup", "emergency_poweroff_status", "not_needed")
        return False
    except CloudProviderError as restore_error:
        state.mark("cleanup_status", "restore_failed")
        state.mark_stage("cleanup", "status", "restore_failed")
        state.add_error("cleanup_restore", restore_error)
        state.add_warning("cleanup", str(restore_error))
        LOGGER.error("cleanup restore failed: %s", restore_error)
        try:
            with state.step("emergency_poweroff"):
                response = adapter.stop_vm(vm)  # type: ignore[attr-defined]
                state.mark(
                    "emergency_poweroff_status",
                    "dry_run" if response.dry_run else "stopped",
                )
                state.mark_stage(
                    "cleanup",
                    "emergency_poweroff_status",
                    "dry_run" if response.dry_run else "stopped",
                )
                LOGGER.warning("emergency poweroff attempted: %s", response.status)
            return False
        except CloudProviderError as stop_error:
            state.mark("emergency_poweroff_status", "failed")
            state.mark_stage("cleanup", "emergency_poweroff_status", "failed")
            state.add_error("emergency_poweroff", stop_error)
            state.add_fatal_error("emergency_poweroff", stop_error)
            LOGGER.critical("emergency poweroff failed: %s", stop_error)
            return True


def _should_defer_final_cleanup(
    options: SingleRunOptions,
    *,
    case_error: BaseException | None,
    evidence_saved: bool,
) -> bool:
    return options.defer_final_cleanup and case_error is None and evidence_saved


def _mark_final_cleanup_deferred(state: RunState) -> None:
    state.mark("cleanup_status", "deferred_to_next_case")
    state.mark("emergency_poweroff_status", "not_needed")
    state.mark_stage("cleanup", "status", "deferred_to_next_case")
    state.mark_stage(
        "cleanup",
        "deferred_reason",
        "next_case_initial_restore_required",
    )
    state.mark_stage("cleanup", "emergency_poweroff_status", "not_needed")
    LOGGER.info("final cleanup deferred to next case initial restore")


def _try_fast_fail_salvage(
    *,
    client: object,
    case_id: str,
    run_dir: Path,
    state: RunState,
    timeout: NetworkTimeoutProfile,
) -> Path | None:
    if client is None:
        state.mark("evidence_export_status", "failed")
        state.mark_stage("evidence", "status", "failed")
        return None
    try:
        with state.step("evidence_fast_fail_salvage"):
            response = client.export_evidence_bundle(  # type: ignore[attr-defined]
                case_id,
                run_dir / f"case_evidence_{case_id}.zip",
                timeout_seconds=timeout.socket_timeout_seconds(),
            )
            output_path = Path(str(response.data.get("output_path", "")))
            state.mark("evidence_export_status", "saved")
            state.mark("evidence_bundle_path", str(output_path))
            state.mark_stage("evidence", "status", "saved")
            state.mark_stage("evidence", "path", str(output_path))
            state.mark_stage("evidence", "sha256", str(response.data.get("sha256", "")))
            state.mark_stage("evidence", "size", response.data.get("size"))
            state.mark_artifact("evidence_bundle", str(output_path))
            LOGGER.warning("fast-fail evidence salvage saved: %s", output_path)
            return output_path
    except GuestAgentError as exc:
        if exc.source == "network":
            state.mark("agent_dead", True)
        state.mark("evidence_export_status", "failed")
        state.mark_stage("evidence", "status", "failed")
        state.add_error("evidence_salvage", exc)
        state.add_warning("evidence_salvage", str(exc))
        LOGGER.warning("fast-fail evidence salvage failed: %s", exc)
    except Exception as exc:
        state.mark("evidence_export_status", "failed")
        state.mark_stage("evidence", "status", "failed")
        state.add_error("evidence_salvage", exc)
        state.add_warning("evidence_salvage", str(exc))
        LOGGER.warning("fast-fail evidence salvage failed: %s", exc)
    return None


def _poll_upload_status(
    client: GuestAgentClient,
    case_id: str,
    *,
    initial_wait_seconds: float,
    poll_interval_seconds: float,
    timeout_seconds: float,
    sleep: SleepFunc,
) -> GuestAgentResponse:
    LOGGER.info(
        "upload saved; waiting %.0fs before polling post-upload state",
        initial_wait_seconds,
    )
    elapsed = 0.0
    if initial_wait_seconds > 0:
        sleep(initial_wait_seconds)
        elapsed = min(initial_wait_seconds, timeout_seconds)

    last_response: GuestAgentResponse | None = None
    while True:
        last_response = client.case_status(
            case_id,
            timeout_seconds=GUEST_CONTROL_TIMEOUT.socket_timeout_seconds(),
        )
        upload_state = str(_extract_upload_state(last_response.data) or "unknown")
        LOGGER.info(
            "upload polling (%.0fs/%.0fs): state=%s",
            elapsed,
            timeout_seconds,
            upload_state,
        )
        if upload_state == "removed_after_save" or elapsed >= timeout_seconds:
            return last_response
        wait_seconds = min(poll_interval_seconds, timeout_seconds - elapsed)
        if wait_seconds <= 0:
            return last_response
        sleep(wait_seconds)
        elapsed += wait_seconds


def _check_security_product_readiness_warning_only(
    *,
    client: GuestAgentClient,
    state: RunState,
    case_id: str,
    product_id: str,
) -> dict[str, str]:
    stage = "security_product_readiness"
    if not product_id:
        reason = "product_id is not configured"
        state.mark_stage(stage, "status", "skipped")
        state.mark_stage(stage, "reason", reason)
        state.mark_stage("environment", "product_readiness_checked", False)
        state.mark_stage("environment", "product_environment_ready", None)
        _write_local_readiness_artifact(
            state,
            {
                "schema_version": "single-run-security-product-readiness.v1",
                "case_id": case_id,
                "product_id": "",
                "status": "skipped",
                "state": "unknown",
                "reason": reason,
                "warnings": [reason],
                "errors": [],
            },
        )
        state.add_warning(stage, reason)
        LOGGER.warning(
            "Security product readiness skipped: %s; continuing because "
            "readiness is warning-only",
            reason,
        )
        return {"status": "skipped", "state": "unknown"}

    try:
        response = client.check_security_product_readiness(
            case_id,
            product_id,
            timeout_seconds=GUEST_CONTROL_TIMEOUT.socket_timeout_seconds(),
        )
    except Exception:
        reason = (
            "readiness API call failed; continuing because readiness is warning-only"
        )
        state.mark_stage(stage, "status", "warning")
        state.mark_stage(stage, "product_id", product_id)
        state.mark_stage(stage, "state", "unknown")
        state.mark_stage(stage, "reason", reason)
        state.mark_stage("environment", "product_readiness_checked", False)
        state.mark_stage("environment", "product_environment_ready", None)
        _write_local_readiness_artifact(
            state,
            {
                "schema_version": "single-run-security-product-readiness.v1",
                "case_id": case_id,
                "product_id": product_id,
                "status": "warning",
                "state": "unknown",
                "reason": reason,
                "warnings": [reason],
                "errors": [],
            },
        )
        state.add_warning(stage, reason)
        LOGGER.warning(
            "Security product readiness check failed; continuing because "
            "readiness is warning-only"
        )
        return {"status": "warning", "state": "unknown"}

    data = response.data
    readiness_state = str(data.get("state") or "unknown")
    status = "ok" if readiness_state == "ready" else "warning"
    confidence = str(data.get("confidence") or "")
    scope = str(data.get("scope") or "")
    protection_state = str(data.get("protection_state") or "")
    checked_at = str(data.get("checked_at_utc") or "")

    state.mark_stage(stage, "status", status)
    state.mark_stage(stage, "product_id", str(data.get("product_id") or product_id))
    state.mark_stage(stage, "state", readiness_state)
    state.mark_stage(stage, "confidence", confidence)
    state.mark_stage(stage, "scope", scope)
    state.mark_stage(stage, "protection_state", protection_state)
    state.mark_stage(stage, "checked_at_utc", checked_at)
    state.mark_stage(stage, "warnings", _safe_readiness_messages(data.get("warnings")))
    state.mark_stage(stage, "errors", _safe_readiness_messages(data.get("errors")))
    state.mark_stage("environment", "product_readiness_checked", True)
    state.mark_stage(
        "environment",
        "product_environment_ready",
        readiness_state == "ready",
    )
    local_payload = dict(data)
    local_payload["schema_version"] = "single-run-security-product-readiness.v1"
    local_payload["status"] = status
    local_payload["warnings"] = _safe_readiness_messages(data.get("warnings"))
    local_payload["errors"] = _safe_readiness_messages(data.get("errors"))
    _write_local_readiness_artifact(state, local_payload)

    if status == "ok":
        LOGGER.info(
            "Security product readiness checked: product=%s state=%s "
            "confidence=%s scope=%s protection_state=%s",
            product_id,
            readiness_state,
            confidence,
            scope,
            protection_state,
        )
        return {"status": status, "state": readiness_state}

    warning = (
        f"security product readiness is {readiness_state}; continuing because "
        "readiness is warning-only"
    )
    state.add_warning(stage, warning)
    LOGGER.warning(
        "Security product readiness warning: product=%s state=%s confidence=%s; "
        "continuing because readiness is warning-only",
        product_id,
        readiness_state,
        confidence,
    )
    return {"status": status, "state": readiness_state}


def _ensure_security_product_readiness_stage_recorded(
    state: RunState,
    *,
    product_id: str,
    case_started: bool,
) -> None:
    stages = state.data.get("stages", {})
    stage_payload = (
        stages.get("security_product_readiness", {}) if isinstance(stages, dict) else {}
    )
    status = stage_payload.get("status") if isinstance(stage_payload, dict) else None
    if status and status != "pending":
        return

    if not product_id:
        reason = "product_id is not configured"
        state.mark_stage("security_product_readiness", "status", "skipped")
        state.mark_stage("security_product_readiness", "reason", reason)
        state.mark_stage("environment", "product_readiness_checked", False)
        state.mark_stage("environment", "product_environment_ready", None)
        return

    if case_started:
        reason = (
            "security product readiness check was not completed before the "
            "case flow ended; continuing because readiness is warning-only"
        )
        state.mark_stage("security_product_readiness", "status", "warning")
        state.mark_stage("security_product_readiness", "product_id", product_id)
        state.mark_stage("security_product_readiness", "state", "unknown")
        state.mark_stage("security_product_readiness", "reason", reason)
        state.mark_stage("environment", "product_readiness_checked", False)
        state.mark_stage("environment", "product_environment_ready", None)


def _safe_readiness_messages(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_sanitize_readiness_message(str(item)) for item in value]


def _write_local_readiness_artifact(
    state: RunState,
    payload: dict[str, Any],
) -> None:
    output_path = state.path.parent / "case_security_product_readiness.json"
    safe_payload = dict(payload)
    safe_payload["warnings"] = _safe_readiness_messages(safe_payload.get("warnings"))
    safe_payload["errors"] = _safe_readiness_messages(safe_payload.get("errors"))
    output_path.write_text(
        json.dumps(safe_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    state.mark_artifact("security_product_readiness", str(output_path))
    state.mark_stage(
        "security_product_readiness",
        "local_artifact_path",
        str(output_path),
    )


def _sanitize_readiness_message(message: str) -> str:
    sanitized = SENSITIVE_MESSAGE_RE.sub("<redacted>", message)
    if len(sanitized) > 500:
        return sanitized[:497] + "..."
    return sanitized


def _execute_after_upload_observation(
    *,
    client: GuestAgentClient,
    case_id: str,
    run_id: str,
    sample_id: str,
    expected_sha256: str,
    stored_filename: str,
    upload_state: str,
    dry_run: bool,
    poll_interval_seconds: float,
    poll_timeout_seconds: float,
    sleep: SleepFunc,
) -> dict[str, str]:
    normalized_upload_state = upload_state.casefold()
    if normalized_upload_state != "stable":
        execution_state = f"skipped_{normalized_upload_state or 'unknown'}"
        reason = (
            "execution skipped because post-upload state is "
            f"{normalized_upload_state or 'unknown'}"
        )
        LOGGER.info("%s", reason)
        return {
            "status": "skipped",
            "execution_state": execution_state,
            "reason": reason,
            "error_source": "none",
            "error_status_code": None,
            "via": "",
            "handler_id": "",
            "execution_mode": "",
        }

    decision = resolve_execution_mode(stored_filename)
    if not decision.enabled:
        execution_state = decision.reason_code or "execution_handler_disabled"
        reason = (
            "execution skipped because handler "
            f"{decision.handler_id} is not enabled for {stored_filename}"
        )
        LOGGER.warning("%s", reason)
        return {
            "status": "skipped",
            "execution_state": execution_state,
            "reason": reason,
            "error_source": "none",
            "error_status_code": None,
            "via": "",
            "handler_id": decision.handler_id,
            "execution_mode": decision.execution_mode,
        }

    try:
        execute_response = client.execute_uploaded_sample(
            case_id=case_id,
            sample_id=sample_id,
            expected_sha256=expected_sha256,
            dry_run=dry_run,
            run_id=run_id,
            handler_id=decision.handler_id,
        )
    except GuestAgentError as exc:
        execution_state = _nonfatal_remote_execution_state(exc)
        if execution_state:
            reason = f"execution action did not start: {exc}"
            LOGGER.warning("%s", reason)
            return {
                "status": "not_started",
                "execution_state": execution_state,
                "reason": reason,
                "error_source": exc.source,
                "error_status_code": exc.status_code,
                "via": "desktop_worker"
                if "desktop worker" in str(exc).casefold()
                else "",
                "handler_id": decision.handler_id,
                "execution_mode": decision.execution_mode,
            }
        raise

    execution_state = str(_extract_execution_state(execute_response.data) or "unknown")
    if dry_run:
        LOGGER.info("execution dry-run completed: %s", execution_state)
        return {
            "status": "dry_run",
            "execution_state": execution_state,
            "reason": "dry-run execution metadata check completed",
            "error_source": "none",
            "error_status_code": None,
            "via": str(execute_response.data.get("execution_via", "")),
            "handler_id": decision.handler_id,
            "execution_mode": decision.execution_mode,
        }
    if execution_state not in {"running", "execution_started"}:
        reason = f"execution action returned {execution_state}; polling skipped"
        LOGGER.warning("%s", reason)
        return {
            "status": "not_started",
            "execution_state": execution_state,
            "reason": reason,
            "error_source": "remote",
            "error_status_code": None,
            "via": str(execute_response.data.get("execution_via", "")),
            "handler_id": decision.handler_id,
            "execution_mode": decision.execution_mode,
        }

    final_observation = _poll_execution_status(
        client,
        case_id,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=poll_timeout_seconds,
        sleep=sleep,
    )
    final_response = final_observation.response
    final_state = final_observation.execution_state or execution_state
    return {
        "status": "observed",
        "execution_state": final_state,
        "reason": "execution was started and observed",
        "error_source": "none",
        "error_status_code": None,
        "via": str(
            final_response.data.get("execution_via")
            or execute_response.data.get("execution_via", "")
        ),
        "handler_id": decision.handler_id,
        "execution_mode": decision.execution_mode,
        **final_observation.to_result_fields(),
    }


def _nonfatal_remote_execution_state(error: GuestAgentError) -> str:
    if error.source != "remote" or error.status_code not in {400, 404, 409}:
        return ""
    text = str(error).casefold()
    for marker, execution_state in NONFATAL_REMOTE_EXECUTION_ERROR_MARKERS.items():
        if marker.casefold() in text:
            return execution_state
    return ""


def _should_wait_after_execution_for_collection(
    execution_result: dict[str, Any],
) -> bool:
    if execution_result.get("status") not in {"observed", "not_started"}:
        return False
    return execution_result.get("execution_state") in TERMINAL_EXECUTION_STATES


def _adaptive_post_execution_collection_delay(
    *,
    client: GuestAgentClient,
    case_id: str,
    product_id: str,
    delay_seconds: float,
    interval_seconds: float,
    sleep: SleepFunc,
) -> dict[str, Any]:
    elapsed = 0.0
    probe_count = 0
    failed_count = 0
    last_state = ""
    while elapsed < delay_seconds:
        wait_seconds = min(interval_seconds, delay_seconds - elapsed)
        if wait_seconds > 0:
            sleep(wait_seconds)
            elapsed += wait_seconds
        probe_count += 1
        try:
            response = client.probe_collection(
                case_id,
                product_id,
                timeout_seconds=GUEST_CONTROL_TIMEOUT.socket_timeout_seconds(),
            )
        except GuestAgentError as exc:
            failed_count += 1
            remaining = max(delay_seconds - elapsed, 0.0)
            if remaining > 0:
                sleep(remaining)
                elapsed += remaining
            LOGGER.warning(
                "product observation probe failed; fallback to fixed delay: %s",
                type(exc).__name__,
            )
            return {
                "product_probe_exit_reason": "probe_failed_fallback_to_fixed_delay",
                "product_probe_elapsed_seconds": elapsed,
                "product_probe_count": probe_count,
                "product_probe_failed_count": failed_count,
                "product_probe_last_state": last_state or "probe_failed",
                "product_probe_warning": (
                    "product observation probe failed; fixed delay completed"
                ),
            }

        data = response.data
        probe_state = str(data.get("probe_state") or "unknown")
        last_state = probe_state
        LOGGER.info(
            "product observation probe (%.0fs/%.0fs): product=%s state=%s",
            elapsed,
            delay_seconds,
            product_id,
            probe_state,
        )
        if probe_state == "strong_signal_observed":
            return {
                "product_probe_exit_reason": "strong_signal_observed",
                "product_probe_elapsed_seconds": elapsed,
                "product_probe_count": probe_count,
                "product_probe_failed_count": failed_count,
                "product_probe_last_state": probe_state,
            }
        if probe_state == "unsupported":
            remaining = max(delay_seconds - elapsed, 0.0)
            if remaining > 0:
                sleep(remaining)
                elapsed += remaining
            return {
                "product_probe_exit_reason": "unsupported_fallback_to_fixed_delay",
                "product_probe_elapsed_seconds": elapsed,
                "product_probe_count": probe_count,
                "product_probe_failed_count": failed_count,
                "product_probe_last_state": probe_state,
            }
        if probe_state == "probe_failed":
            failed_count += 1
            remaining = max(delay_seconds - elapsed, 0.0)
            if remaining > 0:
                sleep(remaining)
                elapsed += remaining
            return {
                "product_probe_exit_reason": "probe_failed_fallback_to_fixed_delay",
                "product_probe_elapsed_seconds": elapsed,
                "product_probe_count": probe_count,
                "product_probe_failed_count": failed_count,
                "product_probe_last_state": probe_state,
                "product_probe_warning": (
                    "product observation probe returned probe_failed; "
                    "fixed delay completed"
                ),
            }

    return {
        "product_probe_exit_reason": "max_delay_elapsed",
        "product_probe_elapsed_seconds": elapsed,
        "product_probe_count": probe_count,
        "product_probe_failed_count": failed_count,
        "product_probe_last_state": last_state or "not_checked",
    }


def _poll_execution_status(
    client: GuestAgentClient,
    case_id: str,
    *,
    poll_interval_seconds: float,
    timeout_seconds: float,
    sleep: SleepFunc,
) -> ExecutionObservationResult:
    elapsed = 0.0
    last_response: GuestAgentResponse | None = None
    while True:
        last_response = client.execution_status(case_id)
        execution_state = str(_extract_execution_state(last_response.data) or "unknown")
        children_count = _children_count(last_response.data)
        LOGGER.info(
            "execution polling (%.0fs/%.0fs): state=%s children=%d",
            elapsed,
            timeout_seconds,
            execution_state,
            children_count,
        )
        if execution_state in TERMINAL_EXECUTION_STATES:
            return _execution_observation_result(
                last_response,
                execution_state=execution_state,
                elapsed_seconds=elapsed,
                exit_reason="terminal_state",
            )
        if elapsed >= timeout_seconds:
            if execution_state == "running":
                timeout_response = client.execution_status(
                    case_id,
                    mark_timeout=True,
                    timeout_seconds=timeout_seconds,
                )
                timeout_state = str(
                    _extract_execution_state(timeout_response.data) or execution_state
                )
                return _execution_observation_result(
                    timeout_response,
                    execution_state=timeout_state,
                    elapsed_seconds=elapsed,
                    exit_reason="timeout",
                )
            return _execution_observation_result(
                last_response,
                execution_state=execution_state,
                elapsed_seconds=elapsed,
                exit_reason="timeout",
            )
        wait_seconds = min(poll_interval_seconds, timeout_seconds - elapsed)
        if wait_seconds <= 0:
            return _execution_observation_result(
                last_response,
                execution_state=execution_state,
                elapsed_seconds=elapsed,
                exit_reason="poll_interval_exhausted",
            )
        sleep(wait_seconds)
        elapsed += wait_seconds


def _execution_observation_result(
    response: GuestAgentResponse,
    *,
    execution_state: str,
    elapsed_seconds: float,
    exit_reason: str,
) -> ExecutionObservationResult:
    return ExecutionObservationResult(
        response=response,
        execution_state=execution_state,
        execution_terminal=execution_state in TERMINAL_EXECUTION_STATES,
        root_pid=_extract_root_pid(response.data),
        children_count=_children_count(response.data),
        elapsed_seconds=elapsed_seconds,
        exit_reason=exit_reason,
    )


def _write_summary_outputs(run_dir: Path, summary: dict[str, Any]) -> Path:
    summary_json = run_dir / "case_summary.json"
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "case_summary.md").write_text(
        render_summary_markdown(summary),
        encoding="utf-8",
    )
    return summary_json


def _hash_file(path: Path) -> tuple[str, str, int]:
    sha256_digest = hashlib.sha256()
    md5_digest = hashlib.md5()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            sha256_digest.update(chunk)
            md5_digest.update(chunk)
    return sha256_digest.hexdigest(), md5_digest.hexdigest(), size


def _render_generated_config(
    *,
    options: SingleRunOptions,
    sample_id: str,
    product_id: str,
    sha256: str,
    md5: str,
) -> str:
    product = _product_profile(product_id)
    mode = "mock" if options.dry_run else "real"
    dry_run = "true" if options.dry_run else "false"
    execution_enabled = "false" if options.dry_run else "true"
    desktop_worker_enabled = (
        "true" if (not options.dry_run and options.require_desktop_worker) else "false"
    )
    return f"""# Generated by cloud-av-agent-lab single-run.
# Non-sensitive control-plane config. Do not add tokens or cloud secrets here.

[lab]
name = "cloud-av-agent-lab-single-run"
artifact_dir = "reports"
local_sample_storage = "forbidden"
require_cloud_isolation = true
max_case_seconds = 900

[cloud]
provider = "tencent-cloud-lighthouse"
mode = {_toml_string(mode)}
dry_run = {dry_run}
region = {_toml_string(options.region)}
credential_profile_env = "TENCENTCLOUD_PROFILE"
secret_id = ""
secret_key = ""
artifact_bucket = "cos://single-run-artifacts-placeholder"
network_profile = "isolated-egress-deny-by-default"
api_endpoint = "https://lighthouse.tencentcloudapi.com"
api_version = "2020-03-24"

[network.proxy]
enabled = false
type = "socks5"
host = "127.0.0.1"
port = 7890

[guest_agent]
enabled = true
base_url = {_toml_string(options.guest_agent_url)}
token_env = "CLOUD_AV_GUEST_AGENT_TOKEN"
timeout_seconds = 10

[guest_agent.execution]
enabled = {execution_enabled}
token_env = "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"
timeout_seconds = 30

[guest_agent.desktop_worker]
enabled = {desktop_worker_enabled}
base_url = {_toml_string(options.desktop_worker_url)}
token_env = "CLOUD_AV_DESKTOP_WORKER_TOKEN"
timeout_seconds = 5
required_for_execution = true
expected_user = "AvTester-Admin"
require_interactive_session = true

[[products]]
id = {_toml_string(product_id)}
display_name = {_toml_string(product["display_name"])}
vendor = {_toml_string(product["vendor"])}
log_paths = {_toml_array(product["log_paths"])}
ui_window_titles = {_toml_array(product["ui_window_titles"])}
detection_keywords = {_toml_array(product["detection_keywords"])}

[[vms]]
id = "single-run-vm"
provider = "tencent-cloud-lighthouse"
region = {_toml_string(options.region)}
image = "img-av-baseline-win"
baseline_snapshot = {_toml_string(options.snapshot_id)}
product_id = {_toml_string(product_id)}
network_profile = "isolated-egress-deny-by-default"
instance_id = {_toml_string(options.instance_id)}

[[samples]]
id = {_toml_string(sample_id)}
sha256 = {_toml_string(sha256)}
md5 = {_toml_string(md5)}
category = "harmless-test"
cloud_object_uri = {_toml_string(f"cos://single-run-placeholder/{sample_id}")}
expected_behaviors = ["upload_observation"]
notes = "Generated single-run EICAR or harmless placeholder reference."
"""


def _product_profile(product_id: str) -> dict[str, Any]:
    profile = SINGLE_RUN_PRODUCT_PROFILES.get(product_id)
    if profile is None:
        raise SingleRunError(f"single-run does not support product={product_id!r}")
    return profile


def supported_single_run_products() -> tuple[str, ...]:
    return tuple(sorted(SINGLE_RUN_PRODUCT_PROFILES))


def _instance_state(response: Any) -> str:
    data = getattr(response, "data", {})
    if not isinstance(data, dict):
        return ""
    for key in ("FinalInstanceStatus", "InstanceStatus"):
        value = data.get(key)
        if isinstance(value, dict):
            return str(value.get("state", "")).upper()
    return ""


def _extract_upload_state(data: dict[str, object]) -> object:
    state = data.get("state")
    if isinstance(state, dict):
        return state.get("upload_state")
    return data.get("upload_state")


def _extract_execution_state(data: dict[str, object]) -> object:
    execution = data.get("execution")
    if isinstance(execution, dict):
        return execution.get("state") or data.get("execution_state")
    return data.get("execution_state")


def _children_count(data: dict[str, object]) -> int:
    execution = data.get("execution")
    children = (
        execution.get("children", [])
        if isinstance(execution, dict)
        else data.get("children", [])
    )
    return len(children) if isinstance(children, list) else 0


def _extract_root_pid(data: dict[str, object]) -> int | None:
    execution = data.get("execution")
    value = (
        execution.get("root_pid")
        if isinstance(execution, dict)
        else data.get("root_pid")
    )
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _final_status(
    case_error: BaseException | None,
    cleanup_failed: bool,
    has_warnings: bool = False,
) -> str:
    if case_error is None and not cleanup_failed and has_warnings:
        return "completed_with_warnings"
    if case_error is None and not cleanup_failed:
        return "completed"
    if case_error is None and cleanup_failed:
        return "completed_with_cleanup_warning"
    if cleanup_failed:
        return "failed_cleanup_failed"
    return "failed"


def _safe_identifier(value: str) -> str:
    cleaned = "".join(
        ch if (ch.isascii() and ch.isalnum()) or ch in {"-", "_", "."} else "-"
        for ch in value.strip()
    )
    cleaned = cleaned.strip(".-_")
    return cleaned or "sample"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"
