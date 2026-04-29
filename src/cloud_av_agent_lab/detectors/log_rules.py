from __future__ import annotations

from cloud_av_agent_lab.core.contracts import AvSignal, ProductProfile


DEFAULT_DETECTION_KEYWORDS = (
    "blocked",
    "quarantine",
    "quarantined",
    "malware",
    "trojan",
    "virus",
    "risk",
    "拦截",
    "隔离",
    "病毒",
    "木马",
    "风险",
)


def parse_log_signals(
    product: ProductProfile,
    source: str,
    text: str,
) -> list[AvSignal]:
    keywords = product.detection_keywords or DEFAULT_DETECTION_KEYWORDS
    signals: list[AvSignal] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        normalized = line.casefold()
        matched = [term for term in keywords if term.casefold() in normalized]
        if not matched:
            continue
        signals.append(
            AvSignal(
                product_id=product.id,
                signal_type="log",
                verdict="detected",
                title=f"{product.display_name} log detection",
                detail=line.strip(),
                confidence=0.75,
                source=f"{source}:{line_number}",
            )
        )

    return signals
