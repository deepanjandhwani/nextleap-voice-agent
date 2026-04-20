from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class BookingStatus(str, Enum):
    TENTATIVE = "tentative"
    WAITLISTED = "waitlisted"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Intent(str, Enum):
    BOOK_NEW = "book_new"
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"
    WHAT_TO_PREPARE = "what_to_prepare"
    CHECK_AVAILABILITY = "check_availability"
    SHARE_PII = "share_pii"
    ASK_INVESTMENT_ADVICE = "ask_investment_advice"
    UNKNOWN = "unknown"


@dataclass
class Slot:
    """A single 30-minute advisor slot in IST."""

    start: datetime  # timezone-aware (Asia/Kolkata)
    label: str  # human-readable with IST


@dataclass
class Booking:
    code: str
    topic: str
    status: BookingStatus
    slot: Slot | None
    requested_day: str | None = None
    requested_time_window: str | None = None
    calendar_hold_id: str | None = None
    sheet_row_id: str | None = None
    email_draft_id: str | None = None
    previous_slot_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResponse:
    response: str
    session_state: str
    booking_code: str | None = None
    secure_link: str | None = None
    status: str | None = None
