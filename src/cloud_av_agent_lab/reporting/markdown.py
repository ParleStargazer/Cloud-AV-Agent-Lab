from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from cloud_av_agent_lab.core.contracts import CaseResult, LabConfig


def _rate(detected: int, total: int) -> str:
    if total == 0:
        return "N/A"
    return f"{detected / total:.1%}"


def render_markdown_report(config: LabConfig, results: list[CaseResult]) -> str:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# {config.policy.name} Report",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Cloud provider: `{config.cloud.provider}`",
        f"- Region: `{config.cloud.region}`",
        f"- Cases: `{len(results)}`",
        "",
        "## Detection Rate",
        "",
        "| Product | Detected | Total | Rate |",
        "|---|---:|---:|---:|",
    ]

    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        grouped[result.case.product.id].append(result)

    for product_id, product_results in sorted(grouped.items()):
        product = config.products[product_id]
        detected = sum(1 for result in product_results if result.detected)
        total = len(product_results)
        lines.append(
            f"| {product.display_name} | {detected} | {total} | {_rate(detected, total)} |"
        )

    lines.extend(
        [
            "",
            "## Case Matrix",
            "",
            "| Case | Sample | Category | Product | Status | Signals |",
            "|---|---|---|---|---|---:|",
        ]
    )

    for result in results:
        case = result.case
        lines.append(
            "| "
            f"{case.id} | {case.sample.id} | {case.sample.category} | "
            f"{case.product.display_name} | {result.status.value} | {len(result.signals)} |"
        )

    competitor_only = _competitor_only_cases(config, results)
    lines.extend(["", "## Competitor-only Detection Candidates", ""])
    if competitor_only:
        lines.extend(f"- `{case_id}`" for case_id in competitor_only)
    else:
        lines.append("- None in this result set.")

    lines.extend(["", "## Evidence", ""])
    for result in results:
        lines.append(f"### {result.case.id}")
        if not result.signals:
            lines.append("- No detection evidence collected.")
            continue
        for signal in result.signals:
            lines.append(
                f"- `{signal.signal_type}` `{signal.verdict}` "
                f"({signal.confidence:.2f}) {signal.title}: {signal.detail}"
            )

    return "\n".join(lines) + "\n"


def _competitor_only_cases(config: LabConfig, results: list[CaseResult]) -> list[str]:
    by_sample: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        by_sample[result.case.sample.id].append(result)

    target_ids = [
        product_id
        for product_id, product in config.products.items()
        if "tencent" in product_id or "tencent" in product.vendor.casefold()
    ]
    if not target_ids:
        return []

    candidates: list[str] = []
    for sample_id, sample_results in by_sample.items():
        target_detected = any(
            result.detected and result.case.product.id in target_ids
            for result in sample_results
        )
        competitor_detected = any(
            result.detected and result.case.product.id not in target_ids
            for result in sample_results
        )
        if competitor_detected and not target_detected:
            candidates.append(sample_id)

    return sorted(candidates)
