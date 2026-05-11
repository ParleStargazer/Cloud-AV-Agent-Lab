from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

from cloud_av_agent_lab.adapters.cloud import CloudProviderError, VMOperationResponse
from cloud_av_agent_lab.adapters.factory import create_cloud_adapter
from cloud_av_agent_lab.config import ConfigError, load_config
from cloud_av_agent_lab.core.contracts import LabConfig
from cloud_av_agent_lab.core.pipeline import TestPipeline
from cloud_av_agent_lab.core.safety import SafetyError, assert_safe_config
from cloud_av_agent_lab.reporting.markdown import render_markdown_report

LIFECYCLE_COMMANDS = {
    "cloud-start": ("start_vm", "StartInstances"),
    "cloud-stop": ("stop_vm", "StopInstances"),
    "cloud-reboot": ("reboot_vm", "RebootInstances"),
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
) -> None:
    lifecycle = subparsers.add_parser(command, help=help_text)
    lifecycle.add_argument("--config", required=True, help="path to TOML config")
    lifecycle.add_argument("--vm-id", required=True, help="VM profile id from config")
    lifecycle.add_argument(
        "--confirm-instance",
        default="",
        help="exact resolved Lighthouse instance id required for real writes",
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
    if args.command in LIFECYCLE_COMMANDS:
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


def _print_cloud_response(response: VMOperationResponse) -> None:
    print(response.message)
    if response.task_id:
        print(f"task_id: {response.task_id}")
    print(f"status: {response.status}")
    if response.data:
        print(json.dumps(response.data, ensure_ascii=False, indent=2))


def _configure_lifecycle_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
