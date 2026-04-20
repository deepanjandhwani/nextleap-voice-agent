from datetime import date, datetime
from zoneinfo import ZoneInfo

from advisor_scheduler.core.session import Session
from advisor_scheduler.llm.response_schema import DayResolutionOutcome
from advisor_scheduler.services.slot_service import (
    SlotService,
    parse_day_token,
    resolve_user_day,
    validate_resolved_day,
)

IST = ZoneInfo("Asia/Kolkata")


def test_monday_morning_slots():
    now = datetime(2026, 4, 17, 10, 0, tzinfo=IST)  # Friday
    svc = SlotService(now_fn=lambda: now)
    # Next Monday from Friday Apr 17 2026 is Apr 20
    d = now.date()
    from datetime import timedelta

    monday = d + timedelta(days=3)
    slots = svc.matching_slots(preferred_day=monday, time_window="morning", limit=2)
    assert len(slots) >= 1
    assert "IST" in slots[0].label


def test_friday_no_slots():
    now = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    svc = SlotService(now_fn=lambda: now)
    slots = svc.matching_slots(preferred_day=now.date(), time_window=None, limit=2)
    assert slots == []


def test_parse_day_token_supports_named_month_dates():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("Can you share any slots for afternoon on 25th April?", ref)
    assert ambiguous is False
    assert resolved is not None
    assert resolved.isoformat() == "2026-04-25"


def test_parse_day_token_supports_spelled_out_ordinal_day():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("Can you share slots on twenty fifth April?", ref)
    assert ambiguous is False
    assert resolved is not None
    assert resolved.isoformat() == "2026-04-25"


def test_parse_day_token_supports_hyphenated_ordinal_with_explicit_year():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("twenty-second April 2026 works for me", ref)
    assert ambiguous is False
    assert resolved is not None
    assert resolved.isoformat() == "2026-04-22"


def test_parse_day_token_supports_spelled_out_ordinal_with_of_month():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("Can you do twenty first of may?", ref)
    assert ambiguous is False
    assert resolved is not None
    assert resolved.isoformat() == "2026-05-21"


def test_parse_day_token_rolls_past_named_month_dates_forward():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("1st April", ref)
    assert ambiguous is False
    assert resolved is not None
    assert resolved.isoformat() == "2027-04-01"


def test_parse_day_token_treats_after_explicit_date_as_exclusive():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("Any day after 30th April", ref)
    assert ambiguous is False
    assert resolved is not None
    assert resolved.isoformat() == "2026-05-01"


def test_parse_day_token_day_after_tomorrow_is_plus_two_not_plus_one():
    """Phrase contains 'tomorrow' as a substring; must not use the plain tomorrow rule."""
    ref = datetime(2026, 4, 19, 10, 0, tzinfo=IST)  # Sunday
    resolved, ambiguous = parse_day_token("Can you show slots for day after tomorrow?", ref)
    assert ambiguous is False
    assert resolved is not None
    assert resolved.isoformat() == "2026-04-21"

    plain_tomorrow, _ = parse_day_token("tomorrow please", ref)
    assert plain_tomorrow.isoformat() == "2026-04-20"


def test_parse_day_token_marks_multiple_distinct_days_ambiguous():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("Can you do Monday or Tuesday afternoon?", ref)
    assert resolved is None
    assert ambiguous is True


def test_parse_day_token_on_or_after_is_inclusive():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("on or after 30th April", ref)
    assert ambiguous is False
    assert resolved is not None
    assert resolved.isoformat() == "2026-04-30"


def test_parse_day_token_before_explicit_date():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("before 30th April", ref)
    assert ambiguous is False
    assert resolved is not None
    assert resolved.isoformat() == "2026-04-29"


def test_parse_day_token_weekday_and_explicit_date_conflict_is_ambiguous():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("Tuesday 20 April", ref)
    assert resolved is None
    assert ambiguous is True


def test_resolve_user_day_uses_structured_llm_iso_when_phrase_is_vague():
    class _FakeLlm:
        def resolve_requested_day(self, prompt: str) -> DayResolutionOutcome:
            return DayResolutionOutcome(date(2026, 5, 1), False, normalized_time_window="morning")

    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    session = Session(session_id="day-resolver-test")
    got = resolve_user_day("prefer early May if possible", ref, llm=_FakeLlm(), session=session)
    assert got.resolved_date == date(2026, 5, 1)
    assert got.normalized_time_window == "morning"


def test_parse_day_token_rejects_explicit_past_calendar_year():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("15 April 2020", ref)
    assert resolved is None
    assert ambiguous is True


def test_parse_day_token_allows_explicit_year_when_boundary_moves_it_to_today():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    resolved, ambiguous = parse_day_token("after 16 April 2026", ref)
    assert ambiguous is False
    assert resolved == date(2026, 4, 17)


def test_validate_resolved_day_uses_ist_calendar():
    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    assert validate_resolved_day(date(2026, 4, 17), ref)
    assert not validate_resolved_day(date(2026, 4, 16), ref)


def test_resolve_user_day_rejects_past_llm_date():
    class _FakeLlm:
        def resolve_requested_day(self, prompt: str) -> DayResolutionOutcome:
            return DayResolutionOutcome(date(2020, 5, 1), False, normalized_time_window=None)

    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    session = Session(session_id="past-llm")
    got = resolve_user_day("prefer early May if possible", ref, llm=_FakeLlm(), session=session)
    assert got.resolved_date is None
    assert got.reason == "past_date"


def test_resolve_user_day_rejects_explicit_past_year_without_llm_call():
    class _ExplodingLlm:
        def resolve_requested_day(self, prompt: str) -> DayResolutionOutcome:
            raise AssertionError("LLM should not be called for explicit past dates")

    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    session = Session(session_id="past-explicit")
    got = resolve_user_day("15 April 2020", ref, llm=_ExplodingLlm(), session=session)
    assert got.resolved_date is None
    assert got.reason == "past_date"


def test_resolve_user_day_uses_deterministic_window_when_present():
    class _FakeLlm:
        def resolve_requested_day(self, prompt: str) -> DayResolutionOutcome:
            return DayResolutionOutcome(date(2026, 5, 1), False, normalized_time_window=None)

    ref = datetime(2026, 4, 17, 10, 0, tzinfo=IST)
    session = Session(session_id="day-resolver-window")
    got = resolve_user_day("prefer early May in the evening", ref, llm=_FakeLlm(), session=session)
    assert got.resolved_date == date(2026, 5, 1)
    assert got.normalized_time_window == "evening"
