from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock
from zoneinfo import ZoneInfo

from advisor_scheduler.types.models import Booking, Slot

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class Session:
    session_id: str
    state: str = "greeting"
    last_activity: datetime = field(default_factory=lambda: datetime.now(IST))

    # Recent chat turns used to rebuild the system prompt each turn.
    history: list[dict[str, str]] = field(default_factory=list)

    # Last assistant text for generic repeat requests
    last_bot_text: str | None = None
    last_availability_windows: list[str] = field(default_factory=list)

    # Flow scratch
    topic: str | None = None
    requested_day_label: str | None = None
    preferred_date: object | None = None  # date
    time_window: str | None = None
    offered_slots: list[Slot] = field(default_factory=list)
    pending_slot: Slot | None = None
    active_booking: Booking | None = None
    code_retries: int = 0

    # Waitlist
    waitlist_topic: str | None = None
    waitlist_day_label: str | None = None

    # Reschedule / cancel target
    target_booking: Booking | None = None

    # Intent and pending action context for the current turn loop
    active_intent: str | None = None
    last_action: str | None = None
    awaiting_confirmation_action: str | None = None
    last_error: str | None = None

class SessionStore:
    def __init__(self, timeout_minutes: int = 20) -> None:
        self._timeout = timedelta(minutes=timeout_minutes)
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> Session:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                s = Session(session_id=session_id)
                self._sessions[session_id] = s
                return s
            if datetime.now(IST) - s.last_activity > self._timeout:
                sid = s.session_id
                s = Session(session_id=sid)
                self._sessions[session_id] = s
            return s

    def touch(self, session: Session) -> None:
        session.last_activity = datetime.now(IST)
