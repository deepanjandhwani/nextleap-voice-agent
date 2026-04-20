from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

from advisor_scheduler.config import Settings, get_settings
from advisor_scheduler.core.session import Session, SessionStore
from advisor_scheduler.core.topics import ALLOWED_TOPICS, match_topic, topics_menu
from advisor_scheduler.guards.compliance import compliance_guard
from advisor_scheduler.integrations.factory import build_adapters
from advisor_scheduler.integrations.protocols import CalendarAdapter, GmailAdapter, SheetsAdapter
from advisor_scheduler.intents.router import parse_booking_code, route_intent
from advisor_scheduler.llm import (
    GeminiClient,
    GeminiTurnDecision,
    LlmClient,
    LlmClientError,
    build_gemini_prompt,
    validate_turn_decision,
)
from advisor_scheduler.orchestration.side_effects import (
    execute_cancel_side_effects,
    execute_reschedule_side_effects,
    execute_side_effects,
)
from advisor_scheduler.services.booking_service import BookingService
from advisor_scheduler.services.slot_service import (
    DayResolutionResult,
    SlotService,
    infer_time_window,
    parse_day_token,
    resolve_user_day,
    validate_resolved_day,
)
from advisor_scheduler.types.models import Booking, BookingStatus, ChatResponse, Intent, Slot

DISCLAIMER = (
    "This conversation is for informational support only and does not provide investment advice."
)
IST = ZoneInfo("Asia/Kolkata")

_FLOW_SWITCH_STATES = frozenset(
    {
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
    }
)

# Invalid booking code attempts before moving to closing (spec: up to 3 retries).
_CODE_RETRY_LIMIT = 3

PREPARE_TEXT = {
    "KYC / Onboarding": (
        "For KYC / onboarding, keep a government ID handy and any prior KYC reference you may have. "
        "The advisor will walk you through next steps; no need to share those details here."
    ),
    "SIP / Mandates": (
        "For SIP / mandates, note your bank mandate status and any recent change requests. "
        "We can discuss process and timelines without reviewing specific investments."
    ),
    "Statements / Tax Docs": (
        "For statements / tax docs, decide which financial year or period you need. "
        "The advisor can explain formats and official retrieval steps."
    ),
    "Withdrawals / Timelines": (
        "For withdrawals / timelines, think through the amount or timeline you want to understand. "
        "We can explain process cutoffs and status checks without giving investment recommendations."
    ),
    "Account Changes / Nominee": (
        "For account changes / nominee updates, know what change you want. "
        "The advisor will explain documentation; please submit documents only through official channels."
    ),
}


@dataclass
class ConversationEngine:
    sessions: SessionStore
    bookings: BookingService
    slots: SlotService
    settings: Settings
    calendar: CalendarAdapter
    sheets: SheetsAdapter
    gmail: GmailAdapter
    llm: LlmClient


def _secure_link(settings: Settings, code: str) -> str | None:
    base = settings.resolved_secure_details_base_url()
    if base is None:
        return None
    parts = urlparse(base)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["code"] = code
    new_query = urlencode(q)
    return urlunparse(
        (parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment)
    )


def _affirmative(text: str) -> bool:
    return bool(
        re.search(
            r"\b(yes|yeah|yep|confirm|go ahead|please do|ok(ay)?|book it|sounds good|"
            r"sure|definitely|do it|proceed|that works|works for me)\b",
            text.lower(),
        )
    )


def _negative(text: str) -> bool:
    return bool(
        re.search(
            r"\b(no|nope|not now|don'?t|cancel that|cancel it|stop|never mind|forget it|"
            r"skip|changed my mind|not this one)\b",
            text.lower(),
        )
    )


def _repeat_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(repeat|say that again|what were the options|come again|tell me again|"
            r"one more time|didn'?t catch that)\b",
            text.lower(),
        )
    )


def _extract_time_references(text: str) -> set[int]:
    refs: set[int] = set()
    # Prefer times with an explicit minute (e.g. 3:30 PM) so day ordinals like "27th" are not read as an hour.
    for match in re.finditer(
        r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ampm>am|pm)\b", text.lower()
    ):
        hour = int(match.group("h"))
        minute = int(match.group("m"))
        ampm = match.group("ampm")
        if not 1 <= hour <= 12 or minute > 59:
            continue
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
        refs.add(hour * 60 + minute)

    for match in re.finditer(r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)\b", text.lower()):
        hour = int(match.group("h"))
        minute = int(match.group("m") or "0")
        ampm = match.group("ampm")
        if not 1 <= hour <= 12 or minute > 59:
            continue
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
        refs.add(hour * 60 + minute)

    for match in re.finditer(r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\b", text.lower()):
        hour = int(match.group("h"))
        minute = int(match.group("m"))
        if hour > 23 or minute > 59:
            continue
        refs.add(hour * 60 + minute)
    return refs


def _extract_bare_clock_times(text: str) -> set[tuple[int, int]]:
    refs: set[tuple[int, int]] = set()
    for match in re.finditer(r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\b", text.lower()):
        hour = int(match.group("h"))
        minute = int(match.group("m"))
        if hour > 23 or minute > 59:
            continue
        refs.add((hour, minute))
    return refs


def _match_offered_slot_choice(session: Session, message: str) -> int | None:
    if not session.offered_slots:
        return None

    time_refs = _extract_time_references(message)
    if time_refs:
        matches = [
            idx
            for idx, slot in enumerate(session.offered_slots)
            if (slot.start.hour * 60 + slot.start.minute) in time_refs
        ]
        if len(matches) == 1:
            return matches[0]

    bare_clock_refs = _extract_bare_clock_times(message)
    if bare_clock_refs:
        matches = []
        for idx, slot in enumerate(session.offered_slots):
            for hour, minute in bare_clock_refs:
                if slot.start.minute != minute:
                    continue
                if slot.start.hour == hour or (1 <= hour <= 12 and slot.start.hour % 12 == hour % 12):
                    matches.append(idx)
                    break
        if len(matches) == 1:
            return matches[0]

    text = message.lower()
    first_patterns = (
        r"\b(first|1st|option\s*1|number\s*1|slot\s*1|the first one|option one|slot one)\b",
        r"\b(earlier|earliest)\b",
    )
    second_patterns = (
        r"\b(second|2nd|option\s*2|number\s*2|slot\s*2|the second one|option two|slot two)\b",
        r"\b(later|latter|last)\b",
    )

    first_match = any(re.search(pattern, text) for pattern in first_patterns)
    second_match = any(re.search(pattern, text) for pattern in second_patterns)
    if first_match and not second_match:
        return 0
    if second_match and not first_match and len(session.offered_slots) > 1:
        return 1
    return None


def _wrap(
    session: Session,
    text: str,
    code: str | None = None,
    link: str | None = None,
    status: str | None = None,
) -> ChatResponse:
    session.last_bot_text = text
    session.history.append({"role": "assistant", "content": text})
    if len(session.history) > 20:
        session.history = session.history[-20:]
    return ChatResponse(
        response=text,
        session_state=session.state,
        booking_code=code,
        secure_link=link,
        status=status,
    )


def _repeat_response(session: Session) -> ChatResponse | None:
    if session.state in {"offer_slots", "offer_slots_reschedule"} and session.offered_slots:
        prefix = "Here are the reschedule options again in IST:" if session.state == "offer_slots_reschedule" else "Here are the options again in IST:"
        opts = " • ".join(f"{i + 1}) {s.label}" for i, s in enumerate(session.offered_slots))
        question = "Which one works?" if session.state == "offer_slots_reschedule" else "Which one should I hold?"
        return _wrap(session, f"{prefix} {opts}. {question}")
    if session.state in {"confirm_slot", "confirm_slot_reschedule"} and session.pending_slot:
        prompt = (
            f"Please confirm this IST slot: {session.pending_slot.label}. Reply yes to reschedule."
            if session.state == "confirm_slot_reschedule"
            else f"Please confirm this IST slot: {session.pending_slot.label}. Reply yes to hold it."
        )
        return _wrap(session, prompt)
    if session.state == "show_availability" and session.preferred_date and session.last_availability_windows:
        joined = " • ".join(session.last_availability_windows[:2])
        return _wrap(
            session,
            f"Here is the availability again in IST for {session.preferred_date.strftime('%A')}: {joined}. Would you like to book one of these?",
        )
    if session.last_bot_text:
        return ChatResponse(response=session.last_bot_text, session_state=session.state)
    return None


def _reset_booking_scratch(session: Session) -> None:
    session.topic = None
    session.preferred_date = None
    session.requested_day_label = None
    session.time_window = None
    session.offered_slots = []
    session.pending_slot = None
    session.waitlist_topic = None
    session.waitlist_day_label = None
    session.awaiting_confirmation_action = None
    session.last_availability_windows = []


def _set_preferred_date(session: Session, resolved_date) -> None:
    session.preferred_date = resolved_date
    session.requested_day_label = resolved_date.strftime("%A")


def _apply_day_resolution_to_session(session: Session, resolution: DayResolutionResult) -> None:
    if resolution.resolved_date is not None:
        _set_preferred_date(session, resolution.resolved_date)
    if resolution.normalized_time_window is not None:
        session.time_window = resolution.normalized_time_window


def _resolve_day_from_message(
    engine: ConversationEngine,
    session: Session,
    message: str,
) -> DayResolutionResult:
    return resolve_user_day(message, engine.slots.now(), llm=engine.llm, session=session)


def _apply_booking_day_from_user_message(
    engine: ConversationEngine,
    session: Session,
    message: str,
    *,
    greeting_prefix: str = "",
) -> ChatResponse | None:
    """Resolve day/time from a booking utterance; return a response if the date blocks booking."""
    resolution = _resolve_day_from_message(engine, session, message)
    if resolution.reason == "past_date":
        return _wrap(
            session,
            f"{greeting_prefix}That date is already in the past. Please share a day from today onward in IST.",
        )
    _apply_day_resolution_to_session(session, resolution)
    return None


def _collect_time_prompt_after_topic(session: Session, topic: str) -> str:
    if session.time_window is not None:
        return (
            f"Got it. For {topic}, which day in IST would you like for the {session.time_window}?"
        )
    return f"Got it. For {topic}, what day and time would you prefer?"


def _calendar_read_failure_message(
    session: Session,
    *,
    reschedule: bool,
    availability_check: bool,
    failure: str | None,
) -> str:
    parts: list[str] = [
        "I couldn't read advisor availability from the calendar right now.",
        "That is usually a temporary connection issue on my side, not a problem with the day you mentioned.",
    ]
    if session.preferred_date is not None:
        if availability_check:
            parts.append(
                f"I still have {session.preferred_date.strftime('%A, %d %B')} in IST for that check."
            )
        else:
            parts.append(
                f"I still have {session.preferred_date.strftime('%A, %d %B')} in IST as your requested day."
            )
    if failure == "mcp_call_timeout":
        parts.append("The calendar check timed out.")
    parts.append("Please try again in a moment.")
    if availability_check:
        tail = "You can also name a different day in IST to check."
    elif reschedule:
        tail = "If it keeps failing, share another day and time window in IST."
    else:
        tail = "If it keeps failing, we can try another day or time window in IST."
    return " ".join(parts) + " " + tail


def _looks_like_booking_change_request(message: str) -> bool:
    lower = message.lower()
    return bool(
        parse_booking_code(message)
        or re.search(r"\b(booking|appointment|slot|code)\b", lower)
        or re.search(r"\b(cancel|reschedule|move|change)\b", lower)
    )


def _looks_like_schedule_correction(message: str) -> bool:
    lower = message.lower()
    return bool(
        infer_time_window(message)
        or re.search(
            r"\b(today|tomorrow|day after tomorrow|next week|monday|tuesday|wednesday|thursday|friday|"
            r"saturday|sunday|\d{1,2}(?:st|nd|rd|th)?(?:\s+of)?\s+"
            r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|"
            r"sep|sept|september|oct|october|nov|november|dec|december)|\d{1,2}[/-]\d{1,2})\b",
            lower,
        )
    )


def _parse_slot_label(label: str | None) -> Slot | None:
    if not label:
        return None
    try:
        start = datetime.strptime(label, "%A, %d %b %Y at %H:%M IST").replace(tzinfo=IST)
    except ValueError:
        return None
    return Slot(start=start, label=label)


def _booking_from_sheet_rows(rows: list, code: str) -> Booking | None:
    latest = None
    for row in rows:
        if row.booking_code.upper() != code.upper():
            continue
        if latest is None or row.updated_at >= latest.updated_at:
            latest = row
    if latest is None:
        return None
    status_text = latest.status.strip().lower()
    try:
        status = BookingStatus(status_text)
    except ValueError:
        status = BookingStatus.FAILED
    return Booking(
        code=latest.booking_code.upper(),
        topic=latest.topic,
        status=status,
        slot=_parse_slot_label(latest.confirmed_slot),
        requested_day=latest.requested_day,
        requested_time_window=latest.requested_time_window,
        calendar_hold_id=latest.calendar_hold_id,
        email_draft_id=latest.email_draft_id,
        previous_slot_label=latest.previous_slot,
    )


def _lookup_booking(engine: ConversationEngine, code: str) -> Booking | None:
    booking = engine.bookings.get(code)
    if booking is not None:
        return booking
    persisted = _booking_from_sheet_rows(engine.sheets.list_rows(), code)
    if persisted is None:
        return None
    return engine.bookings.cache_booking(persisted)


def _is_generic_greeting(message: str) -> bool:
    """True for short hello/hi-style openers (identify_intent) so we skip an LLM round trip."""
    t = message.strip()
    if not t or len(t) > 72:
        return False
    return bool(
        re.match(
            r"^\s*(hi|hello|hey|good\s+(morning|afternoon|evening)|namaste|howdy)"
            r"(?:\s+(there|again))?[\s,.!?]*\s*$",
            t,
            re.I,
        )
    )


def _fallback_response(session: Session, error_message: str) -> ChatResponse:
    session.last_error = error_message
    if session.state == "greeting":
        session.state = "identify_intent"
        return _wrap(
            session,
            f"Hello! {DISCLAIMER} What would you like to do today? You can book, reschedule, cancel, check availability, or ask what to prepare.",
        )
    if session.state == "identify_intent":
        return _wrap(
            session,
            "I can help you book, reschedule, cancel, check availability, or share what to prepare. What would you like to do?",
        )
    if session.state == "collect_topic":
        return _wrap(session, f"Please pick one supported topic: {topics_menu()}")
    if session.state == "collect_topic_prepare":
        return _wrap(session, f"Please choose one supported topic for preparation guidance: {topics_menu()}")
    if session.state == "collect_time":
        return _wrap(session, "Please share one specific day in IST and any preferred time window, like Monday morning or 25 April afternoon.")
    if session.state == "collect_time_reschedule":
        return _wrap(
            session,
            "Please share one specific new day in IST and any preferred time window, like Wednesday afternoon.",
        )
    if session.state == "offer_slots" and session.offered_slots:
        opts = " • ".join(f"{i+1}) {s.label}" for i, s in enumerate(session.offered_slots))
        return _wrap(session, f"I have these options in IST: {opts}. Which one should I hold?")
    if session.state == "offer_slots_reschedule" and session.offered_slots:
        opts = " • ".join(f"{i+1}) {s.label}" for i, s in enumerate(session.offered_slots))
        return _wrap(session, f"Here are the reschedule options in IST: {opts}. Which one works?")
    if session.state == "confirm_slot" and session.pending_slot:
        return _wrap(session, f"Please confirm this IST slot: {session.pending_slot.label}. Reply yes to proceed.")
    if session.state == "confirm_slot_reschedule" and session.pending_slot:
        return _wrap(session, f"Please confirm this IST slot: {session.pending_slot.label}. Reply yes to reschedule.")
    if session.state == "offer_waitlist":
        return _wrap(session, "Would you like me to place you on the waitlist? Please reply yes or no.")
    if session.state == "collect_code":
        return _wrap(session, "Please share your booking code (format NL-XXXX).")
    if session.state == "collect_code_cancel":
        return _wrap(session, "Please share the booking code you want to cancel (format NL-XXXX).")
    if session.state == "confirm_cancel":
        return _wrap(session, "Please reply yes to cancel this booking, or no to keep it.")
    if session.state == "collect_day":
        return _wrap(session, "Which day would you like me to check in IST?")
    if session.state == "show_guidance":
        return _wrap(session, "Would you like help booking a slot for this topic?")
    if session.state == "show_availability":
        return _wrap(session, "Would you like me to help you book one of these IST options?")
    if session.state == "closing":
        return _wrap(session, "Is there anything else I can help you with?")
    if session.state == "idle":
        session.state = "identify_intent"
        return _wrap(session, "What would you like to do today? I can help you book, reschedule, cancel, check availability, or share what to prepare.")
    return _wrap(session, "Could you rephrase that? I can help book, reschedule, cancel, check availability, or share what to prepare.")


def _maybe_mid_flow_intent_switch(engine: ConversationEngine, session: Session, message: str) -> ChatResponse | None:
    sig = route_intent(message)
    if session.state not in _FLOW_SWITCH_STATES:
        return None
    if sig.intent == Intent.RESCHEDULE and sig.confidence >= 0.75 and _looks_like_booking_change_request(message):
        _reset_booking_scratch(session)
        session.target_booking = None
        session.code_retries = 0
        session.state = "collect_code"
        session.active_intent = "reschedule"
        return _wrap(
            session,
            "Sure. I can help you reschedule instead. Please share your booking code (format NL-XXXX).",
        )
    if sig.intent == Intent.CANCEL and sig.confidence >= 0.75 and _looks_like_booking_change_request(message):
        _reset_booking_scratch(session)
        session.target_booking = None
        session.code_retries = 0
        session.state = "collect_code_cancel"
        session.active_intent = "cancel"
        return _wrap(
            session,
            "Okay. I can help cancel. Please share your booking code (format NL-XXXX).",
        )
    if sig.intent == Intent.CHECK_AVAILABILITY and sig.confidence >= 0.7:
        session.active_intent = "check_availability"
        resolution = _resolve_day_from_message(engine, session, message)
        if resolution.resolved_date is not None:
            _apply_day_resolution_to_session(session, resolution)
            return _show_availability(engine, session)
        if resolution.reason == "past_date":
            return _wrap(
                session,
                "That date is already in the past. Please share a day from today onward in IST.",
            )
        session.state = "collect_day"
        return _wrap(session, "Sure. Which day would you like me to check in IST?")
    if sig.intent == Intent.WHAT_TO_PREPARE and sig.confidence >= 0.7:
        _reset_booking_scratch(session)
        session.target_booking = None
        session.active_intent = "what_to_prepare"
        session.state = "collect_topic_prepare"
        return _wrap(session, f"Sure. Which topic would you like preparation guidance for? {topics_menu()}")
    if sig.intent == Intent.BOOK_NEW and sig.confidence >= 0.7 and session.state not in {
        "collect_topic",
        "collect_time",
        "offer_slots",
        "confirm_slot",
        "offer_waitlist",
    }:
        preserve_date = session.state == "show_availability"
        preserve_day = session.preferred_date if preserve_date else None
        preserve_day_label = session.requested_day_label if preserve_date else None
        preserve_window = session.time_window if preserve_date else None
        preserve_topic = session.topic if preserve_date else None
        _reset_booking_scratch(session)
        session.target_booking = None
        session.active_intent = "book_new"
        if preserve_day is not None:
            session.preferred_date = preserve_day
            session.requested_day_label = preserve_day_label
            session.time_window = preserve_window
        if preserve_topic is not None:
            session.topic = preserve_topic
        blocked = _apply_booking_day_from_user_message(engine, session, message)
        if blocked is not None:
            return blocked
        matched_topic = match_topic(message)
        if matched_topic:
            session.topic = matched_topic
        if session.topic:
            if session.preferred_date is not None:
                return _offer_slots(engine, session, reschedule=False)
            session.state = "collect_time"
            return _wrap(session, _collect_time_prompt_after_topic(session, session.topic))
        session.state = "collect_topic"
        return _wrap(session, f"Sure. Let's book a slot. What topic would you like to discuss? {topics_menu()}")
    return None


def _handle_reschedule_code_collection(
    engine: ConversationEngine, session: Session, message: str
) -> ChatResponse | None:
    code = parse_booking_code(message)
    if not code:
        return None

    booking = _lookup_booking(engine, code)
    if not booking:
        session.code_retries += 1
        if session.code_retries >= _CODE_RETRY_LIMIT:
            session.state = "closing"
            return _wrap(
                session,
                "I couldn't find that booking code after a few tries. Is there anything else I can help with?",
            )
        return _wrap(session, "I couldn't find that booking code. Please try again (format NL-XXXX).")

    session.code_retries = 0
    session.target_booking = booking
    session.active_intent = "reschedule"
    session.state = "collect_time_reschedule"
    return _wrap(session, "Got it. What new day and time window would you prefer in IST?")


def _handle_cancel_code_collection(
    engine: ConversationEngine, session: Session, message: str
) -> ChatResponse | None:
    code = parse_booking_code(message)
    if not code:
        return None

    booking = _lookup_booking(engine, code)
    if not booking:
        session.code_retries += 1
        if session.code_retries >= _CODE_RETRY_LIMIT:
            session.state = "closing"
            return _wrap(session, "I couldn't find that booking code. Is there anything else I can help with?")
        return _wrap(session, "I couldn't find that booking code. Please try again.")

    session.code_retries = 0
    session.target_booking = booking
    session.active_intent = "cancel"
    session.state = "confirm_cancel"
    return _wrap(
        session,
        "Please confirm you want to cancel this tentative booking. Reply yes to cancel.",
    )


def _try_deterministic_turn(engine: ConversationEngine, session: Session, message: str) -> ChatResponse | None:
    st = session.state
    sig = route_intent(message)

    if st in {"greeting", "identify_intent"}:
        greeting_prefix = f"Hello! {DISCLAIMER} " if st == "greeting" else ""

        if st == "identify_intent" and _is_generic_greeting(message):
            return _wrap(
                session,
                f"Hello! I'm your NextLeap advisor appointment scheduler. {DISCLAIMER} "
                "I can help you book, reschedule, or cancel appointments, check availability, "
                "or tell you what to prepare. What can I help you with today?",
            )

        if sig.intent == Intent.BOOK_NEW and sig.confidence >= 0.6:
            session.active_intent = "book_new"
            blocked = _apply_booking_day_from_user_message(
                engine, session, message, greeting_prefix=greeting_prefix
            )
            if blocked is not None:
                return blocked
            matched_topic = match_topic(message)
            if matched_topic:
                session.topic = matched_topic
                if session.preferred_date is not None:
                    return _offer_slots(engine, session, reschedule=False)
                session.state = "collect_time"
                return _wrap(
                    session,
                    f"{greeting_prefix}{_collect_time_prompt_after_topic(session, matched_topic)}",
                )
            session.state = "collect_topic"
            return _wrap(
                session,
                f"{greeting_prefix}Certainly. What topic would you like to discuss? "
                f"You can choose from: {topics_menu()}",
            )
        if sig.intent == Intent.RESCHEDULE and sig.confidence >= 0.6:
            session.active_intent = "reschedule"
            session.state = "collect_code"
            session.code_retries = 0
            return _wrap(
                session,
                f"{greeting_prefix}Sure, I can help you reschedule. "
                "Please share your booking code (format NL-XXXX).",
            )
        if sig.intent == Intent.CANCEL and sig.confidence >= 0.6:
            session.active_intent = "cancel"
            session.state = "collect_code_cancel"
            session.code_retries = 0
            return _wrap(
                session,
                f"{greeting_prefix}I can help cancel. "
                "Please share your booking code (format NL-XXXX).",
            )
        if sig.intent == Intent.CHECK_AVAILABILITY and sig.confidence >= 0.6:
            session.active_intent = "check_availability"
            resolution = _resolve_day_from_message(engine, session, message)
            if resolution.resolved_date is not None:
                _apply_day_resolution_to_session(session, resolution)
                return _show_availability(engine, session)
            if resolution.reason == "past_date":
                return _wrap(
                    session,
                    f"{greeting_prefix}That date is already in the past. Please share a day from today onward in IST.",
                )
            session.state = "collect_day"
            return _wrap(
                session,
                f"{greeting_prefix}Which day would you like me to check in IST?",
            )
        if sig.intent == Intent.WHAT_TO_PREPARE and sig.confidence >= 0.6:
            session.active_intent = "what_to_prepare"
            session.state = "collect_topic_prepare"
            return _wrap(
                session,
                f"{greeting_prefix}Which topic would you like preparation guidance for? "
                f"{topics_menu()}",
            )

        if st == "greeting":
            session.state = "identify_intent"
            return _wrap(
                session,
                f"Hello! I'm your NextLeap advisor appointment scheduler. {DISCLAIMER} "
                "I can help you book, reschedule, or cancel appointments, check availability, "
                "or tell you what to prepare. What can I help you with today?",
            )
        return None

    if st == "collect_topic":
        matched = match_topic(message)
        if matched:
            session.topic = matched
            if session.preferred_date is not None:
                return _offer_slots(engine, session, reschedule=False)
            session.state = "collect_time"
            return _wrap(session, _collect_time_prompt_after_topic(session, matched))
        return None

    if st == "collect_topic_prepare":
        matched = match_topic(message)
        if matched:
            session.topic = matched
            return _show_guidance(session)
        return None

    if st in {"collect_time", "collect_time_reschedule"}:
        resolution = _resolve_day_from_message(engine, session, message)
        if resolution.resolved_date is not None:
            _apply_day_resolution_to_session(session, resolution)
            reschedule = st == "collect_time_reschedule"
            return _offer_slots(engine, session, reschedule=reschedule)
        if resolution.reason == "past_date":
            msg = (
                "That date is already in the past. Please share a day from today onward in IST, with any preferred time window."
                if st == "collect_time"
                else "That date is already in the past. Please share a new day from today onward in IST, with any preferred time window."
            )
            return _wrap(session, msg)
        prompt = (
            "Please share one specific day in IST, for example Monday or 25 April, along with any preferred time window."
            if st == "collect_time"
            else "Please share one specific new day in IST, for example Monday or 25 April, along with any preferred time window."
        )
        return _wrap(session, prompt)

    if st == "collect_day":
        resolution = _resolve_day_from_message(engine, session, message)
        if resolution.resolved_date is not None:
            _apply_day_resolution_to_session(session, resolution)
            return _show_availability(engine, session)
        if resolution.reason == "past_date":
            return _wrap(
                session,
                "That date is already in the past. Please share a day from today onward in IST.",
            )
        return _wrap(session, "Please share one specific day in IST, for example Monday or 25 April.")

    if st == "collect_code":
        return _handle_reschedule_code_collection(engine, session, message)
    if st == "collect_code_cancel":
        return _handle_cancel_code_collection(engine, session, message)
    if st in {"offer_slots", "offer_slots_reschedule"} and session.offered_slots:
        selected_idx = _match_offered_slot_choice(session, message)
        if selected_idx is not None:
            session.pending_slot = session.offered_slots[selected_idx]
            reschedule = st == "offer_slots_reschedule" or session.active_intent == "reschedule"
            session.awaiting_confirmation_action = "execute_reschedule" if reschedule else "execute_booking"
            session.state = "confirm_slot_reschedule" if reschedule else "confirm_slot"
            text = (
                f"Let me confirm: {session.pending_slot.label}. Should I go ahead and reschedule to this time?"
                if reschedule
                else f"Let me confirm: {session.pending_slot.label}. Should I go ahead and place the tentative booking?"
            )
            return _wrap(session, text)

        resolution = DayResolutionResult(None, True)
        requested_window = infer_time_window(message)
        if _looks_like_schedule_correction(message):
            resolution = _resolve_day_from_message(engine, session, message)
        if resolution.resolved_date is not None or requested_window is not None:
            if resolution.resolved_date is not None:
                _apply_day_resolution_to_session(session, resolution)
            elif requested_window is not None:
                session.time_window = requested_window
            session.pending_slot = None
            reschedule = st == "offer_slots_reschedule" or session.active_intent == "reschedule"
            return _offer_slots(engine, session, reschedule=reschedule)

    if st == "offer_waitlist":
        if _affirmative(message):
            return _execute_waitlist(engine, session)
        if _negative(message):
            session.state = "collect_time"
            return _wrap(
                session,
                "No problem. Would you like to try a different day or time window?",
            )
        return None

    if st == "confirm_slot" and session.pending_slot:
        selected_idx = _match_offered_slot_choice(session, message)
        if selected_idx is not None:
            if session.offered_slots[selected_idx] != session.pending_slot:
                session.pending_slot = session.offered_slots[selected_idx]
                return _wrap(
                    session,
                    f"Updated. Please confirm this IST slot: {session.pending_slot.label}. Reply yes to proceed.",
                )
        else:
            resolution = DayResolutionResult(None, True)
            requested_window = infer_time_window(message)
            if _looks_like_schedule_correction(message):
                resolution = _resolve_day_from_message(engine, session, message)
            if resolution.resolved_date is not None or requested_window is not None:
                if resolution.resolved_date is not None:
                    _apply_day_resolution_to_session(session, resolution)
                elif requested_window is not None:
                    session.time_window = requested_window
                session.pending_slot = None
                return _offer_slots(engine, session, reschedule=False)
        if _affirmative(message):
            return _execute_booking(engine, session)
        if _negative(message) and session.offered_slots:
            session.pending_slot = None
            session.state = "offer_slots"
            opts = " • ".join(f"{i+1}) {s.label}" for i, s in enumerate(session.offered_slots))
            return _wrap(
                session,
                f"No problem. Here are the options again in IST: {opts}. Which one should I hold?",
            )
        return None

    if st == "confirm_slot_reschedule" and session.pending_slot and session.target_booking:
        selected_idx = _match_offered_slot_choice(session, message)
        if selected_idx is not None:
            if session.offered_slots[selected_idx] != session.pending_slot:
                session.pending_slot = session.offered_slots[selected_idx]
                return _wrap(
                    session,
                    f"Updated. Please confirm this IST slot: {session.pending_slot.label}. Reply yes to reschedule.",
                )
        else:
            resolution = DayResolutionResult(None, True)
            requested_window = infer_time_window(message)
            if _looks_like_schedule_correction(message):
                resolution = _resolve_day_from_message(engine, session, message)
            if resolution.resolved_date is not None or requested_window is not None:
                if resolution.resolved_date is not None:
                    _apply_day_resolution_to_session(session, resolution)
                elif requested_window is not None:
                    session.time_window = requested_window
                session.pending_slot = None
                return _offer_slots(engine, session, reschedule=True)
        if _affirmative(message):
            return _execute_reschedule(engine, session)
        if _negative(message) and session.offered_slots:
            session.pending_slot = None
            session.state = "offer_slots_reschedule"
            opts = " • ".join(f"{i+1}) {s.label}" for i, s in enumerate(session.offered_slots))
            return _wrap(session, f"No problem. Here are the reschedule options again in IST: {opts}. Which one works?")
        return None

    if st == "confirm_cancel" and session.target_booking:
        if _affirmative(message):
            return _execute_cancel(engine, session)
        if _negative(message):
            session.state = "closing"
            return _wrap(session, "Okay — I won't cancel. Is there anything else I can help you with?")
        return None

    if st == "closing":
        if sig.confidence >= 0.6 and sig.intent != Intent.UNKNOWN:
            session.state = "identify_intent"
            return _try_deterministic_turn(engine, session, message) or _wrap(
                session,
                "Sure. What would you like to do next?",
            )
        if _affirmative(message):
            session.state = "identify_intent"
            return _wrap(session, "Sure. What else can I help you with?")
        if _negative(message):
            session.state = "idle"
            return _wrap(session, "Thank you! Have a great day.")
        return None

    if st == "show_guidance":
        if _affirmative(message):
            session.state = "collect_topic"
            return _wrap(
                session,
                f"Great! What topic would you like to book for? {topics_menu()}",
            )
        if _negative(message):
            session.state = "closing"
            return _wrap(session, "No problem. Is there anything else I can help you with?")
        return None

    if st == "show_availability":
        resolution = DayResolutionResult(None, True)
        if _looks_like_schedule_correction(message):
            resolution = _resolve_day_from_message(engine, session, message)
        if resolution.resolved_date is not None:
            _apply_day_resolution_to_session(session, resolution)
            return _show_availability(engine, session)
        if resolution.reason == "past_date":
            return _wrap(
                session,
                "That date is already in the past. Please share a day from today onward in IST.",
            )
        if _affirmative(message):
            if session.topic:
                if session.preferred_date is not None:
                    return _offer_slots(engine, session, reschedule=False)
                session.state = "collect_time"
                return _wrap(session, _collect_time_prompt_after_topic(session, session.topic))
            session.state = "collect_topic"
            return _wrap(
                session,
                f"Let's book a slot. What topic would you like to discuss? {topics_menu()}",
            )
        if _negative(message):
            session.state = "closing"
            return _wrap(session, "No problem. Is there anything else I can help you with?")
        return None

    return None


def process_message(engine: ConversationEngine, session_id: str, message: str) -> ChatResponse:
    session = engine.sessions.get(session_id)
    engine.sessions.touch(session)

    g = compliance_guard(message)
    if not g.ok:
        ab = session.active_booking
        return ChatResponse(
            response=g.message,
            session_state=session.state,
            booking_code=ab.code if ab else None,
            secure_link=_secure_link(engine.settings, ab.code) if ab else None,
            status=ab.status.value if ab else None,
        )

    if session.state == "idle":
        session.state = "identify_intent"

    if _repeat_request(message):
        repeated = _repeat_response(session)
        if repeated is not None:
            return repeated

    session.history.append({"role": "user", "content": message})
    if len(session.history) > 20:
        session.history = session.history[-20:]

    switched = _maybe_mid_flow_intent_switch(engine, session, message)
    if switched is not None:
        return switched

    deterministic = _try_deterministic_turn(engine, session, message)
    if deterministic is not None:
        return deterministic

    try:
        decision = engine.llm.complete_json(build_gemini_prompt(session, message))
    except LlmClientError as exc:
        return _fallback_response(session, str(exc))

    validation_error = validate_turn_decision(decision)
    if validation_error:
        return _fallback_response(session, validation_error)

    return _apply_turn_decision(engine, session, message, decision)


def _apply_turn_decision(
    engine: ConversationEngine,
    session: Session,
    message: str,
    decision: GeminiTurnDecision,
) -> ChatResponse:
    session.last_error = None
    prev_state = session.state
    prev_active_intent = session.active_intent
    session.active_intent = decision.intent
    session.state = decision.next_state
    if decision.next_state in ("collect_code", "collect_code_cancel") and prev_state not in (
        "collect_code",
        "collect_code_cancel",
    ):
        session.code_retries = 0

    if decision.topic in ALLOWED_TOPICS:
        session.topic = decision.topic

    if decision.time_window is not None:
        session.time_window = decision.time_window
    else:
        inferred = infer_time_window(message)
        if inferred is not None:
            session.time_window = inferred

    if decision.booking_code:
        booking = _lookup_booking(engine, decision.booking_code)
        if booking:
            session.target_booking = booking
            session.code_retries = 0
        else:
            session.code_retries += 1

    if decision.resolved_day_iso:
        try:
            d = date.fromisoformat(decision.resolved_day_iso.strip())
            if validate_resolved_day(d, engine.slots.now()):
                _apply_day_resolution_to_session(session, DayResolutionResult(d, False, None, decision.time_window))
        except ValueError:
            pass
    elif decision.requested_day_text:
        resolved_date, ambiguous = parse_day_token(decision.requested_day_text, engine.slots.now())
        if not ambiguous and resolved_date is not None and validate_resolved_day(resolved_date, engine.slots.now()):
            _apply_day_resolution_to_session(session, DayResolutionResult(resolved_date, False, None, decision.time_window))

    if decision.action in {
        "execute_booking",
        "execute_waitlist",
        "execute_reschedule",
        "execute_cancel",
    } and not _affirmative(message):
        session.state = prev_state
        session.active_intent = prev_active_intent
        return _wrap(
            session,
            "Please reply yes to confirm before I make that scheduling change.",
        )

    if decision.action == "offer_slots":
        return _offer_slots(engine, session, reschedule=decision.intent == "reschedule")
    if decision.action == "confirm_pending_slot":
        return _confirm_pending_slot(session, decision)
    if decision.action == "execute_booking":
        return _execute_booking(engine, session)
    if decision.action == "execute_waitlist":
        return _execute_waitlist(engine, session)
    if decision.action == "execute_reschedule":
        return _execute_reschedule(engine, session)
    if decision.action == "execute_cancel":
        return _execute_cancel(engine, session)
    if decision.action == "show_guidance":
        return _show_guidance(session)
    if decision.action == "show_availability":
        return _show_availability(engine, session)

    if decision.close_session:
        session.state = "idle"
    return _wrap(session, decision.reply)


def _offer_slots(engine: ConversationEngine, session: Session, *, reschedule: bool) -> ChatResponse:
    if session.preferred_date is None:
        session.state = "collect_time_reschedule" if reschedule else "collect_time"
        return _wrap(session, "Please share a specific day and time window in IST.")

    slots = engine.slots.matching_slots(
        preferred_day=session.preferred_date,
        time_window=session.time_window,
        limit=2,
    )
    if not engine.slots.last_mcp_freebusy_ok:
        session.offered_slots = []
        session.last_availability_windows = []
        session.state = "collect_time_reschedule" if reschedule else "collect_time"
        msg = _calendar_read_failure_message(
            session,
            reschedule=reschedule,
            availability_check=False,
            failure=engine.slots.last_mcp_freebusy_failure,
        )
        return _wrap(session, msg)

    session.offered_slots = slots
    session.last_availability_windows = []
    if not slots:
        if reschedule:
            session.state = "collect_time_reschedule"
            return _wrap(
                session,
                "I couldn't find alternative slots for that preference in IST. Please try a different day or time window.",
            )
        session.waitlist_topic = session.topic
        session.waitlist_day_label = session.requested_day_label
        session.state = "offer_waitlist"
        return _wrap(
            session,
            "I couldn't find a matching slot right now. I can place you on the waitlist instead. Would you like me to do that?",
        )

    if len(slots) == 1:
        session.pending_slot = slots[0]
        session.awaiting_confirmation_action = "execute_reschedule" if reschedule else "execute_booking"
        session.state = "confirm_slot_reschedule" if reschedule else "confirm_slot"
        text = (
            f"I found one option in IST: {slots[0].label}. Should I go ahead and reschedule to this time?"
            if reschedule
            else f"I found one option in IST: {slots[0].label}. Should I go ahead and hold this slot?"
        )
        return _wrap(session, text)

    session.state = "offer_slots_reschedule" if reschedule else "offer_slots"
    opts = " • ".join(f"{i+1}) {s.label}" for i, s in enumerate(slots))
    text = (
        f"Here are two options in IST: {opts}. Which works?"
        if reschedule
        else f"I found these options in IST: {opts}. Which one should I hold?"
    )
    return _wrap(session, text)


def _confirm_pending_slot(session: Session, decision: GeminiTurnDecision) -> ChatResponse:
    idx = decision.selected_slot_index
    if idx is None or idx < 0 or idx >= len(session.offered_slots):
        return _fallback_response(session, "missing_slot_choice")
    session.pending_slot = session.offered_slots[idx]
    reschedule = session.active_intent == "reschedule"
    session.awaiting_confirmation_action = "execute_reschedule" if reschedule else "execute_booking"
    session.state = "confirm_slot_reschedule" if reschedule else "confirm_slot"
    text = (
        f"Let me confirm: {session.pending_slot.label}. Should I go ahead and reschedule to this time?"
        if reschedule
        else f"Let me confirm: {session.pending_slot.label}. Should I go ahead and place the tentative booking?"
    )
    return _wrap(session, text)


def _secure_link_sentence(settings: Settings, code: str) -> tuple[str | None, str]:
    link = _secure_link(settings, code)
    if link is None:
        return None, "I could not share the secure contact-details link right now because it is not configured."
    # URL stays in API fields for the UI; spoken/chat copy avoids printing the raw link.
    return link, "We will email you a secure link to submit your contact details."


def _execute_booking(engine: ConversationEngine, session: Session) -> ChatResponse:
    if not session.pending_slot:
        session.state = "collect_time"
        return _wrap(session, "Let's pick a time again. What day and time window should I use in IST?")
    booking = engine.bookings.create_booking(
        topic=session.topic or ALLOWED_TOPICS[0],
        status=BookingStatus.TENTATIVE,
        slot=session.pending_slot,
        requested_day=session.requested_day_label,
        requested_time_window=session.time_window,
    )
    result = execute_side_effects(
        settings=engine.settings,
        calendar=engine.calendar,
        sheets=engine.sheets,
        gmail=engine.gmail,
        booking=booking,
        user_intent="book_new",
        action_type="new_booking",
        waitlist=False,
    )
    booking.status = result.final_status
    if result.calendar_id:
        booking.calendar_hold_id = result.calendar_id
    if result.sheet_row_id:
        booking.sheet_row_id = result.sheet_row_id
    if result.draft_id:
        booking.email_draft_id = result.draft_id
    engine.bookings.update_booking(booking)
    session.active_booking = booking
    session.state = "closing"
    session.last_action = "execute_booking"
    session.awaiting_confirmation_action = None
    link, link_sentence = _secure_link_sentence(engine.settings, booking.code)
    text = (
        f"{result.user_message} Your tentative slot is {booking.slot.label if booking.slot else 'not available'}."
        f" Your booking code is {booking.code}. "
        f"{link_sentence} "
        "Is there anything else I can help you with?"
    )
    return _wrap(session, text, code=booking.code, link=link, status=booking.status.value)


def _execute_waitlist(engine: ConversationEngine, session: Session) -> ChatResponse:
    booking = engine.bookings.create_booking(
        topic=session.waitlist_topic or session.topic or ALLOWED_TOPICS[0],
        status=BookingStatus.WAITLISTED,
        slot=None,
        requested_day=session.waitlist_day_label or session.requested_day_label,
        requested_time_window=session.time_window,
    )
    result = execute_side_effects(
        settings=engine.settings,
        calendar=engine.calendar,
        sheets=engine.sheets,
        gmail=engine.gmail,
        booking=booking,
        user_intent="book_new",
        action_type="waitlist",
        waitlist=True,
    )
    booking.status = result.final_status
    if result.calendar_id:
        booking.calendar_hold_id = result.calendar_id
    if result.sheet_row_id:
        booking.sheet_row_id = result.sheet_row_id
    if result.draft_id:
        booking.email_draft_id = result.draft_id
    engine.bookings.update_booking(booking)
    session.active_booking = booking
    session.state = "closing"
    session.last_action = "execute_waitlist"
    link, link_sentence = _secure_link_sentence(engine.settings, booking.code)
    text = (
        f"{result.user_message} Your booking code is {booking.code}. "
        f"{link_sentence} "
        "Is there anything else I can help you with?"
    )
    return _wrap(session, text, code=booking.code, link=link, status=booking.status.value)


def _execute_reschedule(engine: ConversationEngine, session: Session) -> ChatResponse:
    if not session.pending_slot or not session.target_booking:
        session.state = "identify_intent"
        return _wrap(session, "Let's start over. What would you like to do?")
    booking = session.target_booking
    result = execute_reschedule_side_effects(
        settings=engine.settings,
        calendar=engine.calendar,
        sheets=engine.sheets,
        gmail=engine.gmail,
        booking=booking,
        new_slot=session.pending_slot,
        user_intent="reschedule",
    )
    booking.status = result.final_status
    if result.calendar_id:
        booking.calendar_hold_id = result.calendar_id
    if result.sheet_row_id:
        booking.sheet_row_id = result.sheet_row_id
    if result.draft_id:
        booking.email_draft_id = result.draft_id
    engine.bookings.update_booking(booking)
    session.active_booking = booking
    session.state = "closing"
    session.last_action = "execute_reschedule"
    session.awaiting_confirmation_action = None
    link, link_sentence = _secure_link_sentence(engine.settings, booking.code)
    text = (
        f"{result.user_message} The updated slot is {booking.slot.label if booking.slot else 'not available'}. "
        f"{link_sentence} Is there anything else I can help you with?"
    )
    return _wrap(session, text, code=booking.code, link=link, status=booking.status.value)


def _execute_cancel(engine: ConversationEngine, session: Session) -> ChatResponse:
    if not session.target_booking:
        session.state = "identify_intent"
        return _wrap(session, "Please share the booking code you want to cancel (format NL-XXXX).")
    booking = session.target_booking
    result = execute_cancel_side_effects(
        settings=engine.settings,
        calendar=engine.calendar,
        sheets=engine.sheets,
        gmail=engine.gmail,
        booking=booking,
        user_intent="cancel",
    )
    booking.status = result.final_status
    booking.calendar_hold_id = result.calendar_id
    if result.sheet_row_id:
        booking.sheet_row_id = result.sheet_row_id
    if result.draft_id:
        booking.email_draft_id = result.draft_id
    engine.bookings.update_booking(booking)
    session.active_booking = booking
    session.state = "closing"
    session.last_action = "execute_cancel"
    text = f"{result.user_message} Is there anything else I can help you with?"
    return _wrap(session, text, code=booking.code, status=booking.status.value)


def _show_guidance(session: Session) -> ChatResponse:
    topic = session.topic
    if topic not in PREPARE_TEXT:
        session.state = "collect_topic_prepare"
        return _wrap(session, "Please pick a supported topic: " + topics_menu())
    session.state = "show_guidance"
    return _wrap(session, PREPARE_TEXT[topic] + " Would you like help booking a slot?")


def _show_availability(engine: ConversationEngine, session: Session) -> ChatResponse:
    if session.preferred_date is None:
        session.state = "collect_day"
        return _wrap(session, "Which day would you like me to check in IST?")
    wins = engine.slots.availability_windows_for_day(session.preferred_date)
    if not engine.slots.last_mcp_freebusy_ok:
        session.state = "collect_day"
        session.last_availability_windows = []
        return _wrap(
            session,
            _calendar_read_failure_message(
                session,
                reschedule=False,
                availability_check=True,
                failure=engine.slots.last_mcp_freebusy_failure,
            ),
        )
    wins = wins[:2]
    if not wins:
        session.state = "collect_day"
        session.last_availability_windows = []
        cal_hint = "calendar" if engine.settings.use_mcp else "current mock calendar"
        return _wrap(
            session,
            f"I don't see availability on {session.preferred_date.strftime('%A')} in the {cal_hint}. Would you like to try another day?",
        )
    session.state = "show_availability"
    session.last_availability_windows = wins
    joined = " • ".join(wins)
    return _wrap(
        session,
        f"Here is what I see in IST for {session.preferred_date.strftime('%A')}: {joined}. Would you like to book one of these?",
    )


def build_default_engine() -> ConversationEngine:
    settings = get_settings()
    calendar, sheets, gmail = build_adapters(settings)
    return ConversationEngine(
        sessions=SessionStore(timeout_minutes=settings.session_timeout_minutes),
        bookings=BookingService(),
        slots=SlotService(settings=settings),
        settings=settings,
        calendar=calendar,
        sheets=sheets,
        gmail=gmail,
        llm=GeminiClient(settings=settings),
    )
