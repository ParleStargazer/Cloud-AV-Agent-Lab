from __future__ import annotations

from collections.abc import Mapping

from cloud_av_agent_lab.core.contracts import AvSignal, ProductProfile


BEHAVIOR_KEYS = {
    "persistence_blocked": "Persistence behavior blocked",
    "privilege_escalation_blocked": "Privilege escalation behavior blocked",
    "process_injection_blocked": "Process injection behavior blocked",
    "network_connection_blocked": "Outbound network activity blocked",
}


def parse_behavior_signals(
    product: ProductProfile,
    observations: Mapping[str, object],
) -> list[AvSignal]:
    signals: list[AvSignal] = []
    for key, title in BEHAVIOR_KEYS.items():
        if observations.get(key) is not True:
            continue
        signals.append(
            AvSignal(
                product_id=product.id,
                signal_type="behavior",
                verdict="detected",
                title=title,
                detail=f"Normalized observation {key}=true",
                confidence=0.8,
                source="behavior-observations",
            )
        )
    return signals
