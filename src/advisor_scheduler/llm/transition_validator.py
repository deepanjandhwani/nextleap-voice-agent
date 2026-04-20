from __future__ import annotations

from advisor_scheduler.core.topics import ALLOWED_TOPICS
from advisor_scheduler.llm.response_schema import GeminiTurnDecision


def validate_turn_decision(decision: GeminiTurnDecision) -> str | None:
    if decision.topic is not None and decision.topic not in ALLOWED_TOPICS:
        return "unsupported_topic"

    if decision.action in {
        "execute_booking",
        "execute_waitlist",
        "execute_reschedule",
        "execute_cancel",
    } and not decision.reply:
        return "missing_reply"

    if decision.action == "confirm_pending_slot" and decision.selected_slot_index is None:
        return "missing_slot_choice"

    if decision.action in {"execute_reschedule", "execute_cancel"} and not decision.booking_code:
        return "missing_booking_code"

    return None
