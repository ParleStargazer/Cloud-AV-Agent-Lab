from __future__ import annotations

DETECTION_EVENT_IDS = frozenset({1006, 1116})
ACTION_TAKEN_EVENT_IDS = frozenset({1007, 1117})
ACTION_FAILED_EVENT_IDS = frozenset({1118, 1119})
PROTECTION_STATE_CHANGED_EVENT_IDS = frozenset({5000, 5001, 5011, 5012})

CORE_EVENT_IDS = frozenset(
    DETECTION_EVENT_IDS
    | ACTION_TAKEN_EVENT_IDS
    | ACTION_FAILED_EVENT_IDS
    | PROTECTION_STATE_CHANGED_EVENT_IDS
)

EVENT_KIND_BY_ID = {
    **{event_id: "detected" for event_id in DETECTION_EVENT_IDS},
    **{event_id: "action_taken" for event_id in ACTION_TAKEN_EVENT_IDS},
    **{event_id: "action_failed" for event_id in ACTION_FAILED_EVENT_IDS},
    **{
        event_id: "protection_state_changed"
        for event_id in PROTECTION_STATE_CHANGED_EVENT_IDS
    },
}


def event_kind_for_id(event_id: int | None) -> str:
    if event_id is None:
        return "unknown"
    return EVENT_KIND_BY_ID.get(event_id, "unknown")
