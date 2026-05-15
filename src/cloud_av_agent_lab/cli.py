from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Sequence

from cloud_av_agent_lab.adapters.guest_agent_client import (
    GuestAgentClient,
    GuestAgentError,
    GuestAgentResponse,
)
from cloud_av_agent_lab.adapters.cloud import CloudProviderError, VMOperationResponse
from cloud_av_agent_lab.adapters.factory import create_cloud_adapter
from cloud_av_agent_lab.config import ConfigError, load_config
from cloud_av_agent_lab.core.contracts import LabConfig, TestCase
from cloud_av_agent_lab.core.pipeline import TestPipeline
from cloud_av_agent_lab.core.safety import SafetyError, assert_safe_config
from cloud_av_agent_lab.network.client import NetworkClient
from cloud_av_agent_lab.reporting.markdown import render_markdown_report

LIFECYCLE_COMMANDS = {
    "cloud-start": ("start_vm", "StartInstances"),
    "cloud-stop": ("stop_vm", "StopInstances"),
    "cloud-reboot": ("reboot_vm", "RebootInstances"),
}
RESTORE_SNAPSHOT_COMMAND = "cloud-restore-snapshot"
GUEST_UPLOAD_STATUS_INITIAL_WAIT_SECONDS = 10.0
GUEST_UPLOAD_STATUS_POLL_INTERVAL_SECONDS = 2.0
GUEST_UPLOAD_STATUS_TIMEOUT_SECONDS = 30.0
GUEST_EXECUTION_POLL_INTERVAL_SECONDS = 2.0
GUEST_EXECUTION_POLL_TIMEOUT_SECONDS = 60.0
GUEST_EXECUTION_TERMINAL_STATES = {
    "exited_cleanly",
    "exited_with_error",
    "launch_failed",
    "terminated_or_disappeared",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloud-av-agent-lab",
        description="Plan cloud-isolated AV sample testing workflows.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate lab config")
    validate.add_argument("--config", required=True, help="path to TOML config")

    plan = subparsers.add_parser("plan", help="print the dry-run execution plan")
    plan.add_argument("--config", required=True, help="path to TOML config")

    status = subparsers.add_parser(
        "cloud-status",
        help="query one cloud instance status through the configured adapter",
    )
    status.add_argument("--config", required=True, help="path to TOML config")
    status.add_argument("--vm-id", required=True, help="VM profile id from config")

    guest_health = subparsers.add_parser(
        "guest-health",
        help="query cloud-side Guest Agent health",
    )
    guest_health.add_argument("--config", required=True, help="path to TOML config")
    guest_health.add_argument(
        "--vm-id", required=True, help="VM profile id from config"
    )

    guest_prepare = subparsers.add_parser(
        "guest-prepare-case",
        help="ask cloud-side Guest Agent to prepare a harmless case workspace",
    )
    guest_prepare.add_argument("--config", required=True, help="path to TOML config")
    guest_prepare.add_argument(
        "--sample-id", required=True, help="sample id from config"
    )
    guest_prepare.add_argument(
        "--vm-id", required=True, help="VM profile id from config"
    )

    guest_status = subparsers.add_parser(
        "guest-case-status",
        help="query cloud-side Guest Agent case status and recent events",
    )
    guest_status.add_argument("--config", required=True, help="path to TOML config")
    guest_status.add_argument(
        "--vm-id", required=True, help="VM profile id from config"
    )
    guest_status.add_argument("--case-id", required=True, help="prepared case id")

    guest_report = subparsers.add_parser(
        "guest-case-report",
        help="query cloud-side Guest Agent delivery-stage case report",
    )
    guest_report.add_argument("--config", required=True, help="path to TOML config")
    guest_report.add_argument(
        "--vm-id", required=True, help="VM profile id from config"
    )
    guest_report.add_argument("--case-id", required=True, help="prepared case id")

    guest_execution_status = subparsers.add_parser(
        "guest-execution-status",
        help="query cloud-side Guest Agent execution process observation status",
    )
    guest_execution_status.add_argument(
        "--config", required=True, help="path to TOML config"
    )
    guest_execution_status.add_argument(
        "--vm-id", required=True, help="VM profile id from config"
    )
    guest_execution_status.add_argument(
        "--case-id", required=True, help="prepared case id"
    )

    guest_execute = subparsers.add_parser(
        "guest-execute-sample",
        help=(
            "request the controlled Guest Agent sample action; defaults to dry-run "
            "metadata validation"
        ),
    )
    guest_execute.add_argument("--config", required=True, help="path to TOML config")
    guest_execute.add_argument(
        "--vm-id", required=True, help="VM profile id from config"
    )
    guest_execute.add_argument("--case-id", required=True, help="prepared case id")
    guest_execute.add_argument(
        "--sample-id", required=True, help="sample id from config"
    )
    guest_execute.add_argument(
        "--expected-sha256",
        default="",
        help="optional expected sha256; defaults to the configured sample hash",
    )
    guest_execute.add_argument(
        "--real-action",
        action="store_true",
        help=(
            "request execute_uploaded_sample instead of dry-run; requires "
            "[guest_agent.execution].enabled=true and a matching execution token"
        ),
    )
    guest_execute.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=GUEST_EXECUTION_POLL_INTERVAL_SECONDS,
        help="execution-status poll interval after --real-action",
    )
    guest_execute.add_argument(
        "--poll-timeout-seconds",
        type=float,
        default=GUEST_EXECUTION_POLL_TIMEOUT_SECONDS,
        help="maximum seconds to observe execution after --real-action",
    )

    guest_upload = subparsers.add_parser(
        "guest-upload-sample",
        help="upload an EICAR or harmless test file to a prepared Guest Agent case",
    )
    guest_upload.add_argument("--config", required=True, help="path to TOML config")
    guest_upload.add_argument(
        "--vm-id", required=True, help="VM profile id from config"
    )
    guest_upload.add_argument(
        "--sample-id", required=True, help="sample id from config"
    )
    guest_upload.add_argument("--case-id", required=True, help="prepared case id")
    guest_upload.add_argument(
        "--file",
        required=True,
        help="explicit local EICAR or harmless test file path to upload",
    )
    guest_upload.add_argument(
        "--sha256",
        default="",
        help="optional expected sha256 metadata for the uploaded test file",
    )

    _add_lifecycle_parser(
        subparsers,
        "cloud-start",
        "start one cloud instance after safety confirmation",
    )
    _add_lifecycle_parser(
        subparsers,
        "cloud-stop",
        "stop one cloud instance after safety confirmation",
    )
    _add_lifecycle_parser(
        subparsers,
        "cloud-reboot",
        "reboot one cloud instance after safety confirmation",
    )
    _add_lifecycle_parser(
        subparsers,
        RESTORE_SNAPSHOT_COMMAND,
        "restore one cloud instance baseline snapshot after safety confirmation",
        require_snapshot_confirmation=True,
    )

    report = subparsers.add_parser(
        "report-template",
        help="write a Markdown report scaffold from the planned matrix",
    )
    report.add_argument("--config", required=True, help="path to TOML config")
    report.add_argument("--out", required=True, help="output Markdown path")

    return parser


def _add_lifecycle_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    command: str,
    help_text: str,
    require_snapshot_confirmation: bool = False,
) -> None:
    lifecycle = subparsers.add_parser(command, help=help_text)
    lifecycle.add_argument("--config", required=True, help="path to TOML config")
    lifecycle.add_argument("--vm-id", required=True, help="VM profile id from config")
    lifecycle.add_argument(
        "--confirm-instance",
        default="",
        help="exact resolved Lighthouse instance id required for real writes",
    )
    if require_snapshot_confirmation:
        lifecycle.add_argument(
            "--confirm-snapshot",
            default="",
            help="exact configured baseline snapshot id required for restore",
        )
    lifecycle.add_argument(
        "--timeout-seconds",
        type=float,
        default=600.0,
        help="maximum seconds to wait for the target instance state",
    )
    lifecycle.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=5.0,
        help="poll interval for DescribeInstances; real writes require 5-10 seconds",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in LIFECYCLE_COMMANDS or args.command == RESTORE_SNAPSHOT_COMMAND:
        _configure_lifecycle_logging()

    try:
        config = load_config(args.config)
        assert_safe_config(config)
    except (ConfigError, SafetyError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")

    pipeline = TestPipeline(config)

    if args.command == "validate":
        cases = pipeline.build_plan()
        print(
            "ok: "
            f"{len(config.samples)} samples, "
            f"{len(config.products)} products, "
            f"{len(config.vms)} VMs, "
            f"{len(cases)} planned cases"
        )
        return 0

    if args.command == "plan":
        for event in pipeline.dry_run():
            print(event)
        return 0

    if args.command == "cloud-status":
        vm = config.vms.get(args.vm_id)
        if vm is None:
            parser.exit(2, f"error: unknown vm id {args.vm_id!r}\n")
        try:
            response = create_cloud_adapter(config).get_instance_status(vm)
        except CloudProviderError as exc:
            parser.exit(2, f"error: {exc}\n")
        _print_cloud_response(response)
        return 0

    if args.command == "guest-health":
        vm = config.vms.get(args.vm_id)
        if vm is None:
            parser.exit(2, f"error: unknown vm id {args.vm_id!r}\n")
        _ensure_guest_agent_enabled(parser, config)
        try:
            response = _create_guest_agent_client(config).health()
        except GuestAgentError as exc:
            parser.exit(2, _format_guest_error(exc))
        _print_guest_response(response)
        return 0

    if args.command == "guest-prepare-case":
        vm = config.vms.get(args.vm_id)
        if vm is None:
            parser.exit(2, f"error: unknown vm id {args.vm_id!r}\n")
        sample = config.samples.get(args.sample_id)
        if sample is None:
            parser.exit(2, f"error: unknown sample id {args.sample_id!r}\n")
        product = config.products[vm.product_id]
        case = TestCase(
            id=f"{sample.id}__{product.id}",
            sample=sample,
            vm=vm,
            product=product,
        )
        _ensure_guest_agent_enabled(parser, config)
        try:
            response = _create_guest_agent_client(config).prepare_case(case)
        except GuestAgentError as exc:
            parser.exit(2, _format_guest_error(exc))
        _print_guest_response(response)
        return 0

    if args.command == "guest-case-status":
        vm = config.vms.get(args.vm_id)
        if vm is None:
            parser.exit(2, f"error: unknown vm id {args.vm_id!r}\n")
        _ensure_guest_agent_enabled(parser, config)
        try:
            response = _create_guest_agent_client(config).case_status(args.case_id)
        except GuestAgentError as exc:
            parser.exit(2, _format_guest_error(exc))
        _print_guest_response(response)
        return 0

    if args.command == "guest-case-report":
        vm = config.vms.get(args.vm_id)
        if vm is None:
            parser.exit(2, f"error: unknown vm id {args.vm_id!r}\n")
        _ensure_guest_agent_enabled(parser, config)
        try:
            response = _create_guest_agent_client(config).case_report(args.case_id)
        except GuestAgentError as exc:
            parser.exit(2, _format_guest_error(exc))
        _print_guest_response(response)
        return 0

    if args.command == "guest-execution-status":
        vm = config.vms.get(args.vm_id)
        if vm is None:
            parser.exit(2, f"error: unknown vm id {args.vm_id!r}\n")
        _ensure_guest_agent_enabled(parser, config)
        try:
            response = _create_guest_agent_client(config).execution_status(args.case_id)
        except GuestAgentError as exc:
            parser.exit(2, _format_guest_error(exc))
        _print_guest_response(response)
        return 0

    if args.command == "guest-execute-sample":
        vm = config.vms.get(args.vm_id)
        if vm is None:
            parser.exit(2, f"error: unknown vm id {args.vm_id!r}\n")
        sample = config.samples.get(args.sample_id)
        if sample is None:
            parser.exit(2, f"error: unknown sample id {args.sample_id!r}\n")
        _ensure_guest_agent_enabled(parser, config)
        if args.real_action and not config.guest_agent.execution.enabled:
            parser.exit(
                2,
                "error: [Local Check] 本地执行配置未启用或 Token 缺失；"
                "请设置 [guest_agent.execution].enabled=true，并提供执行 "
                "token 环境变量后再使用 --real-action。\n",
            )
        expected_sha256 = args.expected_sha256 or sample.sha256
        if args.real_action:
            _validate_guest_execution_polling_args(parser, args)
        client = _create_guest_agent_client(config)
        try:
            response = client.execute_uploaded_sample(
                case_id=args.case_id,
                sample_id=sample.id,
                expected_sha256=expected_sha256,
                dry_run=not args.real_action,
            )
        except GuestAgentError as exc:
            parser.exit(2, _format_guest_error(exc))
        if args.real_action and _is_remote_execution_disabled(response):
            parser.exit(2, _format_remote_execution_disabled(response))
        _print_guest_response(response)
        if not args.real_action:
            print(
                "info: guest-execute-sample 默认只做 dry-run metadata 校验；"
                "没有启动样本进程。"
            )
        else:
            try:
                execution_response = _poll_guest_execution_status(
                    client=client,
                    case_id=args.case_id,
                    poll_interval_seconds=args.poll_interval_seconds,
                    timeout_seconds=args.poll_timeout_seconds,
                )
            except GuestAgentError as exc:
                parser.exit(2, _format_guest_error(exc))
            _print_guest_response(execution_response)
            message = _execution_state_message(
                _extract_execution_state(execution_response.data)
            )
            if message:
                print(message)
        return 0

    if args.command == "guest-upload-sample":
        vm = config.vms.get(args.vm_id)
        if vm is None:
            parser.exit(2, f"error: unknown vm id {args.vm_id!r}\n")
        sample = config.samples.get(args.sample_id)
        if sample is None:
            parser.exit(2, f"error: unknown sample id {args.sample_id!r}\n")
        _ensure_guest_agent_enabled(parser, config)
        client = _create_guest_agent_client(config)
        try:
            response = client.upload_sample(
                case_id=args.case_id,
                sample_id=sample.id,
                file_path=args.file,
                sha256=args.sha256,
            )
        except GuestAgentError as exc:
            parser.exit(2, _format_guest_error(exc))
        _print_guest_response(response)
        try:
            status_response = _poll_guest_upload_status(client, args.case_id)
        except GuestAgentError as exc:
            parser.exit(2, _format_guest_error(exc))
        _print_guest_response(status_response)
        message = _upload_state_message(_extract_upload_state(status_response.data))
        if message:
            print(message)
        return 0

    if args.command in LIFECYCLE_COMMANDS:
        _validate_lifecycle_polling_args(parser, args)
        vm = config.vms.get(args.vm_id)
        if vm is None:
            parser.exit(2, f"error: unknown vm id {args.vm_id!r}\n")

        probe_adapter = create_cloud_adapter(config, dry_run=True)
        resolved_instance_id = probe_adapter.resolve_instance_id(vm)
        allowed, reasons = _write_execution_guard(
            config,
            resolved_instance_id=resolved_instance_id,
            confirm_instance=args.confirm_instance,
        )
        adapter = create_cloud_adapter(
            config,
            dry_run=not allowed,
            confirmed_instance_id=args.confirm_instance,
            poll_timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        method_name, cloud_action = LIFECYCLE_COMMANDS[args.command]
        if not allowed:
            print(
                "safety: real write not executed; "
                + "; ".join(reasons)
                + f". Planned action: {cloud_action}."
            )
            print(f"resolved_instance_id: {resolved_instance_id}")
        try:
            response = getattr(adapter, method_name)(vm)
        except CloudProviderError as exc:
            parser.exit(2, f"error: {exc}\n")
        _print_cloud_response(response)
        return 0

    if args.command == RESTORE_SNAPSHOT_COMMAND:
        _validate_lifecycle_polling_args(parser, args)
        vm = config.vms.get(args.vm_id)
        if vm is None:
            parser.exit(2, f"error: unknown vm id {args.vm_id!r}\n")

        probe_adapter = create_cloud_adapter(config, dry_run=True)
        resolved_instance_id = probe_adapter.resolve_instance_id(vm)
        allowed, reasons = _restore_execution_guard(
            config,
            resolved_instance_id=resolved_instance_id,
            confirm_instance=args.confirm_instance,
            baseline_snapshot=vm.baseline_snapshot,
            confirm_snapshot=args.confirm_snapshot,
        )
        adapter = create_cloud_adapter(
            config,
            dry_run=not allowed,
            confirmed_instance_id=args.confirm_instance,
            confirmed_snapshot_id=args.confirm_snapshot,
            poll_timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        if not allowed:
            print(
                "safety: real restore not executed; "
                + "; ".join(reasons)
                + ". Planned action: ApplyInstanceSnapshot."
            )
            print(f"resolved_instance_id: {resolved_instance_id}")
            print(f"baseline_snapshot: {vm.baseline_snapshot}")
        try:
            response = adapter.restore_snapshot(vm)
        except CloudProviderError as exc:
            parser.exit(2, f"error: {exc}\n")
        _print_cloud_response(response)
        return 0

    if args.command == "report-template":
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        report = render_markdown_report(config, pipeline.planned_results())
        out.write_text(report, encoding="utf-8")
        print(f"wrote {out}")
        return 0

    parser.error(f"unknown command {args.command!r}")
    return 2


def _validate_lifecycle_polling_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    if args.timeout_seconds <= 0:
        parser.exit(2, "error: --timeout-seconds must be greater than 0\n")
    if not 5.0 <= args.poll_interval_seconds <= 10.0:
        parser.exit(
            2,
            "error: --poll-interval-seconds must be between 5 and 10 seconds\n",
        )


def _validate_guest_execution_polling_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    if args.poll_timeout_seconds <= 0:
        parser.exit(2, "error: --poll-timeout-seconds must be greater than 0\n")
    if args.poll_interval_seconds <= 0:
        parser.exit(2, "error: --poll-interval-seconds must be greater than 0\n")


def _write_execution_guard(
    config: LabConfig,
    resolved_instance_id: str,
    confirm_instance: str,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if config.cloud.mode.casefold() != "real":
        reasons.append("cloud mode is not real")
    if config.cloud.dry_run:
        reasons.append("cloud dry_run is true")
    confirmation = confirm_instance.strip()
    if not confirmation:
        reasons.append("--confirm-instance was not provided")
    elif confirmation != resolved_instance_id:
        reasons.append("--confirm-instance does not match resolved instance id")
    return not reasons, reasons


def _restore_execution_guard(
    config: LabConfig,
    resolved_instance_id: str,
    confirm_instance: str,
    baseline_snapshot: str,
    confirm_snapshot: str,
) -> tuple[bool, list[str]]:
    allowed, reasons = _write_execution_guard(
        config,
        resolved_instance_id=resolved_instance_id,
        confirm_instance=confirm_instance,
    )
    snapshot_confirmation = confirm_snapshot.strip()
    if not snapshot_confirmation:
        reasons.append("--confirm-snapshot was not provided")
    elif snapshot_confirmation != baseline_snapshot:
        reasons.append("--confirm-snapshot does not match configured baseline_snapshot")
    return allowed and not reasons, reasons


def _print_cloud_response(response: VMOperationResponse) -> None:
    print(response.message)
    if response.task_id:
        print(f"task_id: {response.task_id}")
    print(f"status: {response.status}")
    if response.data:
        print(json.dumps(response.data, ensure_ascii=False, indent=2))


def _create_guest_agent_client(config: LabConfig) -> GuestAgentClient:
    return GuestAgentClient(
        config.guest_agent,
        network=NetworkClient.from_config(config.network),
    )


def _ensure_guest_agent_enabled(
    parser: argparse.ArgumentParser,
    config: LabConfig,
) -> None:
    if not config.guest_agent.enabled:
        parser.exit(
            2,
            "error: [Local Check] 本地 Guest Agent 配置未启用；请设置 "
            "[guest_agent].enabled=true，并提供 token 环境变量后再使用 "
            "Guest Agent 命令。\n",
        )


def _print_guest_response(response: GuestAgentResponse) -> None:
    print(
        json.dumps(
            {
                "status": response.status,
                "message": response.message,
                "data": response.data,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _print_guest_upload_response(response: GuestAgentResponse) -> None:
    _print_guest_response(response)
    message = _upload_state_message(response.data.get("upload_state"))
    if message:
        print(message)


def _poll_guest_upload_status(
    client: GuestAgentClient,
    case_id: str,
    initial_wait_seconds: float = GUEST_UPLOAD_STATUS_INITIAL_WAIT_SECONDS,
    poll_interval_seconds: float = GUEST_UPLOAD_STATUS_POLL_INTERVAL_SECONDS,
    timeout_seconds: float = GUEST_UPLOAD_STATUS_TIMEOUT_SECONDS,
) -> GuestAgentResponse:
    print(
        "info: 上传已写入 Guest Agent，等待 "
        f"{initial_wait_seconds:g} 秒后开始轮询安全软件处理结果。"
    )
    elapsed = 0.0
    if initial_wait_seconds > 0:
        time.sleep(initial_wait_seconds)
        elapsed = min(initial_wait_seconds, timeout_seconds)

    last_response: GuestAgentResponse | None = None
    while True:
        last_response = client.case_status(case_id)
        upload_state = str(_extract_upload_state(last_response.data) or "unknown")
        print(
            "[Polling] 检查样本状态 "
            f"({elapsed:g}s/{timeout_seconds:g}s)... 结果: {upload_state}"
        )
        if upload_state == "removed_after_save":
            break
        if elapsed >= timeout_seconds:
            break
        wait_seconds = min(poll_interval_seconds, timeout_seconds - elapsed)
        if wait_seconds <= 0:
            break
        time.sleep(wait_seconds)
        elapsed += wait_seconds

    return last_response


def _poll_guest_execution_status(
    client: GuestAgentClient,
    case_id: str,
    poll_interval_seconds: float = GUEST_EXECUTION_POLL_INTERVAL_SECONDS,
    timeout_seconds: float = GUEST_EXECUTION_POLL_TIMEOUT_SECONDS,
) -> GuestAgentResponse:
    elapsed = 0.0
    last_response: GuestAgentResponse | None = None
    while True:
        last_response = client.execution_status(case_id)
        execution_state = str(_extract_execution_state(last_response.data) or "unknown")
        root_pid = _extract_root_pid(last_response.data)
        children_count = _extract_children_count(last_response.data)
        print(
            "[Execution Polling] 检查执行状态 "
            f"({elapsed:g}s/{timeout_seconds:g}s)... "
            f"state={execution_state} root_pid={root_pid} "
            f"children={children_count}"
        )
        if execution_state in GUEST_EXECUTION_TERMINAL_STATES:
            break
        if elapsed >= timeout_seconds:
            if execution_state == "running":
                last_response = client.execution_status(
                    case_id,
                    mark_timeout=True,
                    timeout_seconds=timeout_seconds,
                )
            break
        wait_seconds = min(poll_interval_seconds, timeout_seconds - elapsed)
        if wait_seconds <= 0:
            break
        time.sleep(wait_seconds)
        elapsed += wait_seconds

    return last_response


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


def _extract_root_pid(data: dict[str, object]) -> object:
    execution = data.get("execution")
    if isinstance(execution, dict):
        return execution.get("root_pid") or data.get("root_pid")
    return data.get("root_pid")


def _extract_children_count(data: dict[str, object]) -> int:
    execution = data.get("execution")
    children: object
    if isinstance(execution, dict):
        children = execution.get("children", [])
    else:
        children = data.get("children", [])
    return len(children) if isinstance(children, list) else 0


def _upload_state_message(upload_state: object) -> str:
    state = str(upload_state or "")
    if state == "stable":
        return "info: 轮询结束，样本仍为 stable，判定为样本存活。"
    if state == "removed_after_save":
        return "info: 轮询期间检测到 removed_after_save，判定为拦截成功。"
    if state == "locked_or_busy":
        return (
            "warning: 上传成功，但文件可能正在被安全软件占用；"
            "建议稍后运行 guest-case-status 查询。"
        )
    return ""


def _execution_state_message(execution_state: object) -> str:
    state = str(execution_state or "")
    if state == "exited_cleanly":
        return "info: 执行观测显示 root 进程正常退出。"
    if state == "exited_with_error":
        return "warning: 执行观测显示 root 进程以非零退出码结束。"
    if state == "launch_failed":
        return "error: 执行启动失败，详见 execution-status 与事件日志。"
    if state == "terminated_or_disappeared":
        return (
            "info: 进程已退出或不可观察；这本身不等同于杀软拦截，"
            "需结合后续评测证据判断。"
        )
    if state == "timeout_still_running":
        return (
            "info: 轮询窗口结束时进程仍在运行，已记录为 "
            "timeout_still_running；这不等同于失败或拦截。"
        )
    return ""


def _format_guest_error(error: GuestAgentError) -> str:
    source = getattr(error, "source", "remote")
    if source == "local":
        message = f"error: [Local Check] 本地执行配置未启用或 Token 缺失：{error}\n"
    elif source == "network":
        message = f"error: [Network] {error}\n"
    else:
        message = f"error: [Remote Agent] 云端拒绝或无法处理请求：{error}\n"
    if _should_hint_prepare_case(error):
        message += (
            "hint: 请确认 case_id 是否正确，或是否已运行 "
            "guest-prepare-case 初始化工作区。\n"
        )
    return message


def _format_remote_execution_disabled(response: GuestAgentResponse) -> str:
    data = response.data
    timeout = data.get("execution_timeout_seconds")
    timeout_text = f"，timeout={timeout}" if timeout is not None else ""
    return (
        "error: [Remote Agent] 云端拒绝了执行请求（执行未启用或 Token 不匹配）："
        f"{response.message}{timeout_text}。\n"
        "hint: 请确认云端 Guest Agent 启动时已添加 --enable-execution-actions，"
        "并设置 CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN；随后重启云端 Guest Agent。\n"
    )


def _is_remote_execution_disabled(response: GuestAgentResponse) -> bool:
    return str(response.data.get("execution_state", "")).casefold() == (
        "execution_disabled"
    )


def _should_hint_prepare_case(error: GuestAgentError) -> bool:
    text = str(error).casefold()
    return (
        error.status_code == 404
        or "case workspace does not exist" in text
        or "guest-prepare-case" in text
    )


def _configure_lifecycle_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
