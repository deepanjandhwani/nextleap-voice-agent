from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

AllowedState = Literal[
    "greeting",
    "identify_intent",
    "collect_topic",
    "collect_time",
    "offer_slots",
    "confirm_slot",
    "offer_waitlist",
    "collect_code",
    "collect_time_reschedule",
    "offer_slots_reschedule",
    "confirm_slot_reschedule",
    "collect_code_cancel",
    "confirm_cancel",
    "collect_topic_prepare",
    "show_guidance",
    "collect_day",
    "show_availability",
    "closing",
    "idle",
]

AllowedAction = Literal[
    "none",
    "offer_slots",
    "confirm_pending_slot",
    "execute_booking",
    "execute_waitlist",
    "execute_reschedule",
    "execute_cancel",
    "show_guidance",
    "show_availability",
]

AllowedIntent = Literal[
    "book_new",
    "reschedule",
    "cancel",
    "what_to_prepare",
    "check_availability",
    "unknown",
]

AllowedTimeWindow = Literal["morning", "afternoon", "evening"]

ALLOWED_STATES: list[str] = list(get_args(AllowedState))
ALLOWED_ACTIONS: list[str] = list(get_args(AllowedAction))
ALLOWED_INTENTS: list[str] = list(get_args(AllowedIntent))


@dataclass(frozen=True)
class DayResolutionOutcome:
    resolved_date: date | None
    is_ambiguous: bool
    reason: str | None = None
    normalized_time_window: str | None = None


class GeminiTurnDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1)
    next_state: AllowedState
    intent: AllowedIntent = "unknown"
    action: AllowedAction = "none"
    topic: str | None = None
    requested_day_text: str | None = None
    resolved_day_iso: str | None = None
    time_window: AllowedTimeWindow | None = None
    selected_slot_index: int | None = None
    booking_code: str | None = None
    needs_clarification: bool = False
    asks_for_confirmation: bool = False
    close_session: bool = False


class DayResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_date_iso: str | None = Field(default=None)
    is_ambiguous: bool = False
    reason: str | None = Field(default=None)
    normalized_time_window: AllowedTimeWindow | None = Field(default=None)
