from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from cloud_av_agent_lab.adapters.cloud import CloudProviderError
from cloud_av_agent_lab.adapters.factory import create_cloud_adapter
from cloud_av_agent_lab.config import ConfigError, load_config
from cloud_av_agent_lab.core.pipeline import TestPipeline
from cloud_av_agent_lab.core.safety import SafetyError, assert_safe_config
from cloud_av_agent_lab.reporting.markdown import render_markdown_report


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

    report = subparsers.add_parser(
        "report-template",
        help="write a Markdown report scaffold from the planned matrix",
    )
    report.add_argument("--config", required=True, help="path to TOML config")
    report.add_argument("--out", required=True, help="output Markdown path")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
        print(response.message)
        if response.task_id:
            print(f"task_id: {response.task_id}")
        print(f"status: {response.status}")
        if response.data:
            print(json.dumps(response.data, ensure_ascii=False, indent=2))
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
