import types
from datetime import date, timedelta

from advisor_scheduler.config import Settings
from advisor_scheduler.core.engine import ConversationEngine, process_message
from advisor_scheduler.core.session import SessionStore
from advisor_scheduler.integrations.google_workspace.stubs import CalendarStub, GmailStub, SheetsStub
from advisor_scheduler.llm import StubLlmClient
from advisor_scheduler.llm.response_schema import DayResolutionOutcome, GeminiTurnDecision
from advisor_scheduler.services.booking_service import BookingService
from advisor_scheduler.services.slot_service import SlotService


def turn(
    *,
    reply: str,
    next_state: str,
    intent: str = "unknown",
    action: str = "none",
    topic: str | None = None,
    requested_day_text: str | None = None,
    resolved_day_iso: str | None = None,
    time_window: str | None = None,
    selected_slot_index: int | None = None,
    booking_code: str | None = None,
    needs_clarification: bool = False,
    asks_for_confirmation: bool = False,
    close_session: bool = False,
) -> GeminiTurnDecision:
    return GeminiTurnDecision(
        reply=reply,
        next_state=next_state,
        intent=intent,
        action=action,
        topic=topic,
        requested_day_text=requested_day_text,
        resolved_day_iso=resolved_day_iso,
        time_window=time_window,
        selected_slot_index=selected_slot_index,
        booking_code=booking_code,
        needs_clarification=needs_clarification,
        asks_for_confirmation=asks_for_confirmation,
        close_session=close_session,
    )


# ---------------------------------------------------------------------------
# TC-1: happy path booking — fully deterministic, 0 LLM calls
# ---------------------------------------------------------------------------


def test_happy_booking_transcript(engine, llm_stub):
    s = "t1"
    r1 = process_message(engine, s, "I want to book")
    assert "informational support" in r1.response.lower()
    assert "topic" in r1.response.lower()

    r2 = process_message(engine, s, "KYC onboarding")
    assert "day" in r2.response.lower() or "time" in r2.response.lower()

    r3 = process_message(engine, s, "Monday morning")
    assert "IST" in r3.response

    # Monday morning has 1 slot (10:00) → auto confirm_slot
    assert engine.sessions.get(s).state == "confirm_slot"

    r4 = process_message(engine, s, "yes")
    assert "NL-" in r4.response
    assert "secure" in r4.response.lower() or "http" in r4.response

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-6: waitlist decline — fully deterministic
# ---------------------------------------------------------------------------


def test_waitlist_decline(engine, llm_stub):
    s = "t2"
    process_message(engine, s, "book appointment")
    process_message(engine, s, "statements tax")

    r = process_message(engine, s, "Friday morning")
    assert "waitlist" in r.response.lower()

    r2 = process_message(engine, s, "no")
    assert "another day" in r2.response.lower() or "different" in r2.response.lower()

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# Regression: explicit calendar date + time window
# ---------------------------------------------------------------------------


def test_booking_accepts_explicit_calendar_date_without_reprompt(engine, llm_stub):
    s = "explicit-date"
    process_message(engine, s, "book appointment")
    process_message(engine, s, "kyc onboarding")

    r = process_message(engine, s, "Can you share any slots for afternoon on 25th April?")
    assert "specific day and time window" not in r.response.lower()
    assert "waitlist" in r.response.lower()
    assert engine.sessions.get(s).preferred_date.isoformat() == "2026-04-25"

    assert llm_stub.responses == []


def test_booking_preserves_date_from_first_message(engine, llm_stub):
    s = "carry-date-1"
    r1 = process_message(engine, s, "I want to book for 21 April")
    assert "topic" in r1.response.lower()
    sess = engine.sessions.get(s)
    assert sess.preferred_date is not None
    assert sess.preferred_date.isoformat() == "2026-04-21"

    r2 = process_message(engine, s, "KYC onboarding")
    assert "what day and time would you prefer" not in r2.response
    assert "IST" in r2.response
    assert sess.state in {"offer_slots", "confirm_slot", "offer_waitlist"}

    assert llm_stub.responses == []


def test_booking_first_turn_topic_and_preferred_day(engine, llm_stub):
    s = "topic-and-day"
    r1 = process_message(engine, s, "Book KYC onboarding for Monday morning")
    sess = engine.sessions.get(s)
    assert sess.topic == "KYC / Onboarding"
    assert sess.preferred_date.isoformat() == "2026-04-20"
    assert sess.time_window == "morning"
    assert sess.state in {"offer_slots", "confirm_slot", "offer_waitlist"}
    assert "IST" in r1.response

    assert llm_stub.responses == []


def test_booking_mcp_read_failure_preserves_requested_day(fixed_now, llm_stub):
    settings = Settings(
        secure_details_base_url="https://secure.nextleap.test/details",
        advisor_email="a@example.com",
        session_timeout_minutes=20,
        use_mcp=True,
        google_calendar_id="primary",
        mcp_google_config="{}",
    )
    engine = ConversationEngine(
        sessions=SessionStore(timeout_minutes=20),
        bookings=BookingService(),
        slots=SlotService(now_fn=lambda: fixed_now, settings=settings),
        settings=settings,
        calendar=CalendarStub(),
        sheets=SheetsStub(),
        gmail=GmailStub(),
        llm=llm_stub,
    )
    s = "mcp-fail-book"

    def _fail_matching_slots(self, *, preferred_day, time_window, limit):
        self._last_mcp_freebusy_ok = False
        self._last_mcp_freebusy_failure = "mcp_tool_error"
        return []

    engine.slots.matching_slots = types.MethodType(_fail_matching_slots, engine.slots)

    process_message(engine, s, "book for 21 April")
    r = process_message(engine, s, "kyc onboarding")
    lower = r.response.lower()
    assert "couldn't read" in lower
    assert "21" in r.response or "april" in lower
    assert "requested day" in lower or "still have" in lower
    assert engine.sessions.get(s).preferred_date.isoformat() == "2026-04-21"


# ---------------------------------------------------------------------------
# TC-11: session timeout resets greeting
# ---------------------------------------------------------------------------


def test_session_timeout_fresh_greeting(engine, llm_stub):
    s = "t3"
    process_message(engine, s, "hello")
    sess = engine.sessions.get(s)
    sess.last_activity -= timedelta(minutes=30)

    r = process_message(engine, s, "hello again")
    assert "informational support" in r.response.lower()

    assert llm_stub.responses == []


def test_identify_intent_plain_greeting_skips_llm(engine, llm_stub):
    """After idle, state is identify_intent; a bare hello should not require Gemini."""
    s = "idle-hello"
    sess = engine.sessions.get(s)
    sess.state = "identify_intent"
    r = process_message(engine, s, "Hello!")
    assert "NextLeap" in r.response
    assert "informational support" in r.response.lower()
    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-6b: waitlist consent creates booking — fully deterministic
# ---------------------------------------------------------------------------


def test_waitlist_consent_creates_booking(engine, llm_stub):
    s = "tw"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc onboarding")
    process_message(engine, s, "Friday morning")

    r = process_message(engine, s, "yes")
    assert "NL-" in r.response
    assert engine.sheets.rows
    assert engine.gmail.drafts

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-12: PII blocked before LLM
# ---------------------------------------------------------------------------


def test_pii_blocked_before_llm(engine, llm_stub):
    r = process_message(engine, "pii", "My email is user@example.com")
    assert "personal" in r.response.lower() or "share" in r.response.lower()
    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-10: invalid booking code retries — fully deterministic
# ---------------------------------------------------------------------------


def test_invalid_code_retries(engine, llm_stub):
    s = "t4"
    process_message(engine, s, "reschedule")
    process_message(engine, s, "NL-BAD0")
    process_message(engine, s, "NL-BAD1")

    r = process_message(engine, s, "NL-BAD2")
    assert "anything else" in r.response.lower() or "help" in r.response.lower()

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-13: investment advice refusal (compliance guard)
# ---------------------------------------------------------------------------


def test_investment_advice_refusal(engine, llm_stub):
    r = process_message(engine, "inv-1", "Should I sell my mutual funds now?")
    assert "investment advice" in r.response.lower() or "not able to provide" in r.response.lower()
    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-14: confirmation required before booking side effect
# ---------------------------------------------------------------------------


def test_confirmation_required_before_booking(engine, llm_stub):
    s = "guard-confirm"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")

    r3 = process_message(engine, s, "Monday morning")
    assert "confirm" in r3.response.lower() or "IST" in r3.response
    assert engine.sessions.get(s).active_booking is None
    assert engine.sessions.get(s).state == "confirm_slot"

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# Slot selection by time — fully deterministic
# ---------------------------------------------------------------------------


def test_offered_slot_can_be_selected_by_time_without_llm(engine, llm_stub):
    s = "select-by-time"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")

    r3 = process_message(engine, s, "Monday")
    assert "Which one should I hold" in r3.response

    r4 = process_message(engine, s, "Can you book the 2:00 one?")
    assert "Let me confirm:" in r4.response
    assert "14:00 IST" in r4.response
    assert engine.sessions.get(s).state == "confirm_slot"

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# Slot selection by ordinal — fully deterministic
# ---------------------------------------------------------------------------


def test_offered_slot_can_be_selected_by_ordinal_without_llm(engine, llm_stub):
    s = "select-by-ordinal"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Monday")

    r4 = process_message(engine, s, "second option")
    assert "Let me confirm:" in r4.response
    assert "14:00 IST" in r4.response
    assert engine.sessions.get(s).state == "confirm_slot"

    assert llm_stub.responses == []


def test_confirm_slot_prefers_offered_time_over_schedule_correction(engine, llm_stub):
    """Date + time in one message must match an offered slot before schedule-correction heuristics."""
    s = "confirm-date-time"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Monday")
    assert engine.sessions.get(s).state == "offer_slots"

    r = process_message(engine, s, "20 April 2026 2:00 PM please")
    assert "Let me confirm" in r.response
    assert "14:00" in r.response
    assert engine.sessions.get(s).state == "confirm_slot"
    assert llm_stub.responses == []


def test_collect_time_rejects_past_day_from_llm(engine, llm_stub):
    s = "past-collect"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    llm_stub.day_resolution_responses.append(DayResolutionOutcome(date(2020, 5, 1), False))
    r = process_message(engine, s, "prefer early May 2020 if possible")
    assert "past" in r.response.lower()
    assert engine.sessions.get(s).state == "collect_time"


# ---------------------------------------------------------------------------
# TC-2: reschedule happy path — fully deterministic
# ---------------------------------------------------------------------------


def test_reschedule_happy_path(engine, llm_stub):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from advisor_scheduler.types.models import BookingStatus, Slot

    IST = ZoneInfo("Asia/Kolkata")
    booking = engine.bookings.create_booking(
        topic="KYC / Onboarding",
        status=BookingStatus.TENTATIVE,
        slot=Slot(
            start=datetime(2026, 4, 13, 10, 0, tzinfo=IST),
            label="Monday, 13 Apr 2026 at 10:00 IST",
        ),
        requested_day="Monday",
        requested_time_window="morning",
    )

    s = "reschedule-happy"
    r1 = process_message(engine, s, "reschedule my appointment")
    assert "booking code" in r1.response.lower()

    r2 = process_message(engine, s, booking.code)
    assert "new day" in r2.response.lower() or "time" in r2.response.lower()

    r3 = process_message(engine, s, "Wednesday morning")
    assert "IST" in r3.response
    assert engine.sessions.get(s).state == "confirm_slot_reschedule"

    r4 = process_message(engine, s, "yes")
    assert "NL-" in r4.response or "rescheduled" in r4.response.lower()
    assert engine.sessions.get(s).state == "closing"

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-3: cancel happy path — fully deterministic
# ---------------------------------------------------------------------------


def test_cancel_happy_path(engine, llm_stub):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from advisor_scheduler.types.models import BookingStatus, Slot

    IST = ZoneInfo("Asia/Kolkata")
    booking = engine.bookings.create_booking(
        topic="SIP / Mandates",
        status=BookingStatus.TENTATIVE,
        slot=Slot(
            start=datetime(2026, 4, 13, 11, 0, tzinfo=IST),
            label="Monday, 13 Apr 2026 at 11:00 IST",
        ),
        requested_day="Monday",
        requested_time_window="morning",
    )

    s = "cancel-happy"
    process_message(engine, s, "cancel my booking")

    r2 = process_message(engine, s, booking.code)
    assert "confirm" in r2.response.lower() or "cancel" in r2.response.lower()
    assert engine.sessions.get(s).state == "confirm_cancel"

    r3 = process_message(engine, s, "yes")
    assert "cancel" in r3.response.lower()
    assert engine.sessions.get(s).state == "closing"

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-9: invalid booking code for cancel (retry then close)
# ---------------------------------------------------------------------------


def test_invalid_code_cancel_retries(engine, llm_stub):
    s = "cancel-bad"
    process_message(engine, s, "cancel")
    process_message(engine, s, "NL-BAD0")
    process_message(engine, s, "NL-BAD1")

    r = process_message(engine, s, "NL-BAD2")
    assert "anything else" in r.response.lower() or "help" in r.response.lower()
    assert engine.sessions.get(s).state == "closing"

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-5: check availability — fully deterministic
# ---------------------------------------------------------------------------


def test_check_availability(engine, llm_stub):
    s = "avail-1"
    r = process_message(engine, s, "what slots are available Thursday?")
    assert "IST" in r.response
    assert "Thursday" in r.response

    assert llm_stub.responses == []


def test_check_availability_mcp_failure_returns_to_collect_day(engine, llm_stub):
    s = "avail-failure"

    def _fail_closed(day):
        engine.slots._last_mcp_freebusy_ok = False
        return []

    engine.slots.availability_windows_for_day = _fail_closed

    r = process_message(engine, s, "what slots are available Thursday?")
    assert "couldn't read advisor availability" in r.response.lower()
    assert engine.sessions.get(s).state == "collect_day"

    r2 = process_message(engine, s, "Monday")
    assert "couldn't read advisor availability" in r2.response.lower()
    assert engine.sessions.get(s).state == "collect_day"


def test_ambiguous_booking_day_uses_day_resolution_before_general_llm(engine, llm_stub):
    s = "ambiguous-booking-day"
    process_message(engine, s, "book appointment")
    process_message(engine, s, "kyc onboarding")

    llm_stub.day_resolution_responses.append(DayResolutionOutcome(date(2026, 4, 20), False))
    r = process_message(engine, s, "Could we do early next week in the morning?")
    assert "IST" in r.response
    assert engine.sessions.get(s).preferred_date.isoformat() == "2026-04-20"
    assert llm_stub.responses == []
    assert llm_stub.day_resolution_responses == []


def test_collect_day_clarifies_when_day_resolution_stays_ambiguous(engine, llm_stub):
    s = "ambiguous-availability-day"
    process_message(engine, s, "check availability")

    llm_stub.day_resolution_responses.append(DayResolutionOutcome(None, False))
    r = process_message(engine, s, "sometime next week")
    assert "specific day in IST" in r.response
    assert engine.sessions.get(s).state == "collect_day"
    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-5b: check availability — no slots → stay in collect_day
# ---------------------------------------------------------------------------


def test_check_availability_no_slots_stays_in_collect_day(engine, llm_stub):
    s = "avail-none"
    r = process_message(engine, s, "what's available Friday?")
    assert engine.sessions.get(s).state == "collect_day"
    assert "another day" in r.response.lower() or "try" in r.response.lower()

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-7: unsupported topic redirects to allowed list
# ---------------------------------------------------------------------------


def test_unsupported_topic_redirects(engine, llm_stub):
    s = "bad-topic"
    r = process_message(engine, s, "I want to prepare for retirement planning")
    assert "KYC" in r.response or "supported" in r.response.lower() or "topic" in r.response.lower()

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-27: mid-flow intent switch (booking → cancel)
# ---------------------------------------------------------------------------


def test_mid_flow_intent_switch_to_cancel(engine, llm_stub):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from advisor_scheduler.types.models import BookingStatus, Slot

    IST = ZoneInfo("Asia/Kolkata")
    booking = engine.bookings.create_booking(
        topic="Withdrawals / Timelines",
        status=BookingStatus.TENTATIVE,
        slot=Slot(
            start=datetime(2026, 4, 13, 14, 0, tzinfo=IST),
            label="Monday, 13 Apr 2026 at 14:00 IST",
        ),
        requested_day="Monday",
        requested_time_window="afternoon",
    )

    s = "switch-cancel"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc onboarding")

    r = process_message(engine, s, f"actually cancel my booking {booking.code}")
    assert engine.sessions.get(s).state in ("collect_code_cancel", "confirm_cancel", "closing")
    assert "cancel" in r.response.lower()

    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# TC-12b: PII — PAN and Aadhaar blocked
# ---------------------------------------------------------------------------


def test_pan_blocked(engine, llm_stub):
    r = process_message(engine, "pan-1", "My PAN is ABCDE1234F")
    assert not r.response.lower().startswith("hello")
    assert "account" in r.response.lower() or "identifier" in r.response.lower() or "collect" in r.response.lower()
    assert llm_stub.responses == []


def test_aadhaar_blocked(engine, llm_stub):
    r = process_message(engine, "adh-1", "Aadhaar: 1234 5678 9012")
    assert "account" in r.response.lower() or "identifier" in r.response.lower() or "collect" in r.response.lower()
    assert llm_stub.responses == []


# ---------------------------------------------------------------------------
# Closing flow — deterministic yes/no
# ---------------------------------------------------------------------------


def test_closing_yes_continues(engine, llm_stub):
    s = "close-yes"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Monday morning")
    process_message(engine, s, "yes")
    assert engine.sessions.get(s).state == "closing"

    r = process_message(engine, s, "yes")
    assert engine.sessions.get(s).state == "identify_intent"

    assert llm_stub.responses == []


def test_closing_no_ends(engine, llm_stub):
    s = "close-no"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Monday morning")
    process_message(engine, s, "yes")
    assert engine.sessions.get(s).state == "closing"

    r = process_message(engine, s, "no")
    assert engine.sessions.get(s).state == "idle"
    assert "great day" in r.response.lower() or "thank" in r.response.lower()

    assert llm_stub.responses == []


def test_llm_execute_booking_requires_yes_and_preserves_confirm_state(engine, llm_stub):
    s = "llm-confirm-guard"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Monday morning")
    assert engine.sessions.get(s).state == "confirm_slot"

    llm_stub.responses.append(
        turn(
            reply="Please reply yes to confirm before I make that scheduling change.",
            next_state="closing",
            intent="book_new",
            action="execute_booking",
        )
    )
    r = process_message(engine, s, "carry on")
    assert "reply yes" in r.response.lower()
    assert engine.sessions.get(s).state == "confirm_slot"
    assert engine.sessions.get(s).active_booking is None

    r2 = process_message(engine, s, "yes")
    assert "NL-" in r2.response
    assert engine.sessions.get(s).state == "closing"


def test_reschedule_confirmation_rejection_returns_to_options(engine, llm_stub):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from advisor_scheduler.types.models import BookingStatus, Slot

    ist = ZoneInfo("Asia/Kolkata")
    booking = engine.bookings.create_booking(
        topic="KYC / Onboarding",
        status=BookingStatus.TENTATIVE,
        slot=Slot(
            start=datetime(2026, 4, 13, 10, 0, tzinfo=ist),
            label="Monday, 13 Apr 2026 at 10:00 IST",
        ),
        requested_day="Monday",
        requested_time_window="morning",
    )

    s = "reschedule-no"
    process_message(engine, s, "reschedule")
    process_message(engine, s, booking.code)
    process_message(engine, s, "Wednesday")
    assert engine.sessions.get(s).state == "offer_slots_reschedule"

    process_message(engine, s, "second option")
    assert engine.sessions.get(s).state == "confirm_slot_reschedule"
    r = process_message(engine, s, "changed my mind")
    assert engine.sessions.get(s).state == "offer_slots_reschedule"
    assert engine.sessions.get(s).pending_slot is None
    assert "options again" in r.response.lower()


def test_closing_state_carries_new_intent_without_extra_prompt(engine, llm_stub):
    s = "closing-switch"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Monday morning")
    process_message(engine, s, "yes")
    assert engine.sessions.get(s).state == "closing"

    r = process_message(engine, s, "I want to check availability for Thursday")
    assert engine.sessions.get(s).state == "show_availability"
    assert "Thursday" in r.response
    assert "what else can i help" not in r.response.lower()


def test_repeat_request_replays_slot_options_not_generic_last_text(engine, llm_stub):
    s = "repeat-options"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    r = process_message(engine, s, "Monday")
    assert engine.sessions.get(s).state == "offer_slots"
    assert "Which one should I hold?" in r.response

    repeated = process_message(engine, s, "come again")
    assert "options again in IST" in repeated.response
    assert "1)" in repeated.response and "2)" in repeated.response


def test_repeat_request_replays_pending_confirmation(engine, llm_stub):
    s = "repeat-confirm"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Monday")
    process_message(engine, s, "second option")
    assert engine.sessions.get(s).state == "confirm_slot"

    repeated = process_message(engine, s, "tell me again")
    assert "Please confirm this IST slot" in repeated.response
    assert "14:00 IST" in repeated.response


def test_affirmative_variant_confirms_waitlist(engine, llm_stub):
    s = "waitlist-sure"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Friday morning")

    r = process_message(engine, s, "sure")
    assert "NL-" in r.response
    assert engine.sessions.get(s).state == "closing"


def test_negative_variant_declines_waitlist(engine, llm_stub):
    s = "waitlist-nevermind"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Friday morning")

    r = process_message(engine, s, "never mind")
    assert engine.sessions.get(s).state == "collect_time"
    assert "different day" in r.response.lower() or "different" in r.response.lower()


def test_cancel_that_is_local_rejection_not_cancel_intent(engine, llm_stub):
    s = "cancel-that-local"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Monday")
    assert engine.sessions.get(s).state == "offer_slots"

    r = process_message(engine, s, "cancel that")
    assert engine.sessions.get(s).state == "offer_slots"
    assert "booking code" not in r.response.lower()


def test_schedule_correction_updates_slot_search_without_restart(engine, llm_stub):
    s = "schedule-correction"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Monday")
    assert engine.sessions.get(s).state == "offer_slots"

    r = process_message(engine, s, "actually Wednesday afternoon")
    assert engine.sessions.get(s).state == "confirm_slot"
    assert "15:00 IST" in r.response


def test_prepare_flow_can_switch_back_to_booking(engine, llm_stub):
    s = "prepare-to-book"
    process_message(engine, s, "what should I prepare")
    assert engine.sessions.get(s).state == "collect_topic_prepare"

    r = process_message(engine, s, "actually I want to book")
    assert engine.sessions.get(s).state == "collect_topic"
    assert "topic" in r.response.lower()


def test_booking_flow_can_switch_to_prepare(engine, llm_stub):
    s = "booking-to-prepare"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")

    r = process_message(engine, s, "wait, what should I prepare instead?")
    assert engine.sessions.get(s).state == "collect_topic_prepare"
    assert "preparation guidance" in r.response.lower()


def test_prepare_phrasing_prepared_with_no_booking_code_prompt(engine, llm_stub):
    """Regression: 'prepared with' phrasing must enter what_to_prepare, not reschedule/code flow."""
    s = "prepare-prepared-with"
    r = process_message(engine, s, "What all do I need to be prepared with?")
    sess = engine.sessions.get(s)
    assert sess.state == "collect_topic_prepare"
    assert sess.active_intent == "what_to_prepare"
    assert "booking code" not in r.response.lower()
    assert "NL-" not in r.response
    assert "topic" in r.response.lower() or "KYC" in r.response

    assert llm_stub.responses == []


def test_prepare_flow_reaches_topic_guidance(engine, llm_stub):
    s = "prepare-full-guidance"
    process_message(engine, s, "What should I have ready before the call?")
    r2 = process_message(engine, s, "KYC onboarding")
    assert engine.sessions.get(s).state == "show_guidance"
    assert "KYC" in r2.response or "government ID" in r2.response
    assert "Would you like help booking" in r2.response
    assert llm_stub.responses == []


def test_reschedule_lookup_uses_persisted_sheet_rows(engine, llm_stub):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from advisor_scheduler.integrations.google_workspace.stubs import SheetsRow

    ist = ZoneInfo("Asia/Kolkata")
    engine.bookings = type(engine.bookings)()
    engine.sheets.rows.append(
        SheetsRow(
            created_at=datetime(2026, 4, 18, 9, 0, tzinfo=ist),
            updated_at=datetime(2026, 4, 18, 9, 0, tzinfo=ist),
            booking_code="NL-P123",
            topic="KYC / Onboarding",
            intent="book_new",
            requested_day="Monday",
            requested_time_window="morning",
            confirmed_slot="Monday, 20 Apr 2026 at 10:00 IST",
            timezone="Asia/Kolkata",
            status="tentative",
            source="advisor_scheduler",
            calendar_hold_id="evt-1",
            email_draft_id="draft-1",
            action_type="new_booking",
        )
    )

    s = "persisted-lookup"
    process_message(engine, s, "reschedule")
    r = process_message(engine, s, "NL-P123")
    assert engine.sessions.get(s).state == "collect_time_reschedule"
    assert "new day" in r.response.lower()


def test_invalid_secure_link_configuration_uses_safe_fallback(engine, llm_stub):
    engine.settings.secure_details_base_url = "https://example.com/details"
    s = "invalid-secure-link"
    process_message(engine, s, "book")
    process_message(engine, s, "kyc")
    process_message(engine, s, "Monday morning")

    r = process_message(engine, s, "yes")
    assert "not configured" in r.response.lower()
    assert r.secure_link is None
