from datetime import datetime
from zoneinfo import ZoneInfo

from advisor_scheduler.config import Settings
from advisor_scheduler.integrations.google_workspace.stubs import CalendarStub, GmailStub, SheetsStub
from advisor_scheduler.orchestration.side_effects import (
    execute_cancel_side_effects,
    execute_reschedule_side_effects,
    execute_side_effects,
)
from advisor_scheduler.types.models import Booking, BookingStatus, Slot

IST = ZoneInfo("Asia/Kolkata")


def _slot():
    start = datetime(2026, 4, 20, 10, 0, tzinfo=IST)
    return Slot(start=start, label="Monday test IST")


def test_side_effects_happy_path():
    settings = Settings(advisor_email="x@example.com")
    cal, sheets, gmail = CalendarStub(), SheetsStub(), GmailStub()
    b = Booking(
        code="NL-TEST1",
        topic="KYC / Onboarding",
        status=BookingStatus.TENTATIVE,
        slot=_slot(),
        requested_day="Monday",
        requested_time_window="morning",
    )
    r = execute_side_effects(
        settings=settings,
        calendar=cal,
        sheets=sheets,
        gmail=gmail,
        booking=b,
        user_intent="book_new",
        action_type="new_booking",
        waitlist=False,
    )
    assert r.success
    assert cal.calls
    assert sheets.rows
    assert gmail.drafts
    assert sheets.rows[0].email_draft_id == "draft-1"


def test_partial_failure_sheets():
    settings = Settings(advisor_email="x@example.com")
    cal, sheets, gmail = CalendarStub(), SheetsStub(), GmailStub()
    sheets.fail_next = True
    b = Booking(
        code="NL-TEST2",
        topic="KYC / Onboarding",
        status=BookingStatus.TENTATIVE,
        slot=_slot(),
        requested_day="Monday",
        requested_time_window="morning",
    )
    r = execute_side_effects(
        settings=settings,
        calendar=cal,
        sheets=sheets,
        gmail=gmail,
        booking=b,
        user_intent="book_new",
        action_type="new_booking",
        waitlist=False,
    )
    assert not r.success
    assert r.partial_failure
    assert r.final_status == BookingStatus.FAILED


def test_reschedule_updates_hold_when_calendar_id_present():
    settings = Settings(advisor_email="x@example.com")
    cal, sheets, gmail = CalendarStub(), SheetsStub(), GmailStub()
    slot1 = _slot()
    slot2 = Slot(
        start=datetime(2026, 4, 20, 14, 0, tzinfo=IST),
        label="Monday 14:00 IST",
    )
    b = Booking(
        code="NL-RS1",
        topic="KYC / Onboarding",
        status=BookingStatus.TENTATIVE,
        slot=slot1,
        requested_day="Monday",
        requested_time_window="morning",
        calendar_hold_id="evt-orig",
    )
    r = execute_reschedule_side_effects(
        settings=settings,
        calendar=cal,
        sheets=sheets,
        gmail=gmail,
        booking=b,
        new_slot=slot2,
        user_intent="reschedule",
    )
    assert r.success
    assert len(cal.update_calls) == 1
    assert cal.update_calls[0][0] == "evt-orig"
    assert not cal.calls  # no duplicate create
    assert sheets.rows[0].email_draft_id == "draft-1"


def test_cancel_deletes_hold_when_calendar_id_present():
    settings = Settings(advisor_email="x@example.com")
    cal, sheets, gmail = CalendarStub(), SheetsStub(), GmailStub()
    b = Booking(
        code="NL-CX1",
        topic="KYC / Onboarding",
        status=BookingStatus.TENTATIVE,
        slot=_slot(),
        requested_day="Monday",
        requested_time_window="morning",
        calendar_hold_id="evt-del",
    )
    r = execute_cancel_side_effects(
        settings=settings,
        calendar=cal,
        sheets=sheets,
        gmail=gmail,
        booking=b,
        user_intent="cancel",
    )
    assert r.success
    assert cal.delete_calls == ["evt-del"]
    assert not cal.calls
    assert sheets.rows[0].email_draft_id == "draft-1"
