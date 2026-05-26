from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionPolicy:
    allow_batch_script: bool = True
    allow_powershell_script: bool = False


@dataclass(frozen=True)
class ExecutionModeDecision:
    handler_id: str
    execution_mode: str
    enabled: bool
    reason_code: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "handler_id": self.handler_id,
            "execution_mode": self.execution_mode,
            "enabled": self.enabled,
            "reason_code": self.reason_code,
        }


def resolve_execution_mode(
    stored_filename: str,
    policy: ExecutionPolicy | None = None,
) -> ExecutionModeDecision:
    active_policy = policy or ExecutionPolicy()
    suffix = Path(stored_filename).suffix.casefold()
    if suffix == ".exe":
        return ExecutionModeDecision(
            handler_id="pe_executable",
            execution_mode="direct_process",
            enabled=True,
        )
    if suffix in {".bat", ".cmd"}:
        return ExecutionModeDecision(
            handler_id="batch_script",
            execution_mode="script_via_cmd",
            enabled=active_policy.allow_batch_script,
            reason_code=""
            if active_policy.allow_batch_script
            else "execution_handler_disabled",
        )
    if suffix == ".ps1":
        return ExecutionModeDecision(
            handler_id="powershell_script",
            execution_mode="powershell_script",
            enabled=active_policy.allow_powershell_script,
            reason_code=""
            if active_policy.allow_powershell_script
            else "execution_handler_disabled",
        )
    return ExecutionModeDecision(
        handler_id="unsupported",
        execution_mode="unsupported",
        enabled=False,
        reason_code="unsupported_file_type",
    )
