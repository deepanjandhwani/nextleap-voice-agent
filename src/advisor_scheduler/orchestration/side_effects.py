from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from advisor_scheduler.config import Settings
from advisor_scheduler.integrations.google_workspace.stubs import (
    CalendarHoldRequest,
    CalendarHoldResult,
    GmailDraftRequest,
    GmailDraftResult,
    SheetsRow,
)
from advisor_scheduler.integrations.protocols import CalendarAdapter, GmailAdapter, SheetsAdapter
from advisor_scheduler.types.models import Booking, BookingStatus, Slot

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class OrchestrationResult:
    success: bool
    partial_failure: bool
    user_message: str
    calendar_id: str | None
    sheet_row_id: str | None
    draft_id: str | None
    final_status: BookingStatus


def _now() -> datetime:
    return datetime.now(IST)


def execute_side_effects(
    *,
    settings: Settings,
    calendar: CalendarAdapter,
    sheets: SheetsAdapter,
    gmail: GmailAdapter,
    booking: Booking,
    user_intent: str,
    action_type: str,
    waitlist: bool = False,
) -> OrchestrationResult:
    """
    Invokes calendar, sheets, and gmail adapters (stubs or MCP via ``build_adapters``).
    Called after explicit user confirmation.
    """
    slot_label = booking.slot.label if booking.slot else None
    title = f"Advisor Q&A — {booking.topic} — {booking.code}"
    meta = {
        "booking_code": booking.code,
        "topic": booking.topic,
        "booking_type": action_type,
        "source": "advisor_scheduler",
        "user_intent": user_intent,
    }

    cal_ok = True
    cal_id: str | None = None
    if booking.slot and not waitlist:
        start = booking.slot.start
        end = start + timedelta(minutes=30)
        hold = CalendarHoldRequest(
            title=title,
            start_time=start,
            end_time=end,
            timezone="Asia/Kolkata",
            status=booking.status.value,
            metadata=meta,
        )
        cres = calendar.create_hold(hold)
        cal_ok = cres.success
        cal_id = cres.external_id

    gres = _gmail_draft(settings, gmail, booking, user_intent, waitlist)
    row = SheetsRow(
        created_at=_now(),
        updated_at=_now(),
        booking_code=booking.code,
        topic=booking.topic,
        intent=user_intent,
        requested_day=booking.requested_day,
        requested_time_window=booking.requested_time_window,
        confirmed_slot=slot_label,
        timezone="Asia/Kolkata",
        status=booking.status.value,
        source="advisor_scheduler",
        notes="waitlist" if waitlist else None,
        calendar_hold_id=cal_id,
        email_draft_id=gres.draft_id,
        previous_slot=booking.previous_slot_label,
        action_type=action_type,
    )
    sres = sheets.append_row(row)

    partial = (not cal_ok and booking.slot and not waitlist) or (not sres.success) or (not gres.success)
    ok = sres.success and gres.success and (cal_ok or waitlist or not booking.slot)

    if not ok and not partial:
        final = BookingStatus.FAILED
    elif partial:
        final = BookingStatus.FAILED
    else:
        final = booking.status

    msg = "You're all set."
    if partial or not ok:
        msg = (
            "Something went wrong while saving your booking details. "
            "Please try again in a few minutes or contact support if it continues."
        )

    return OrchestrationResult(
        success=ok and not partial,
        partial_failure=partial,
        user_message=msg,
        calendar_id=cal_id,
        sheet_row_id=sres.row_identifier,
        draft_id=gres.draft_id,
        final_status=final,
    )


def _gmail_draft(
    settings: Settings,
    gmail: GmailAdapter,
    booking: Booking,
    user_intent: str,
    waitlist: bool,
) -> GmailDraftResult:
    if waitlist:
        subject = f"Waitlist Request — {booking.topic} — {booking.code}"
        body = (
            f"Booking code: {booking.code}\n"
            f"Topic: {booking.topic}\n"
            f"No suitable slot was available; user opted into waitlist.\n"
            f"Status: waitlisted\n"
            f"Source: advisor_scheduler\n"
            f"Contact details will be collected separately through the secure link."
        )
    else:
        subject = f"Tentative Advisor Booking — {booking.topic} — {booking.code}"
        slot_txt = booking.slot.label if booking.slot else "n/a"
        body = (
            f"Booking code: {booking.code}\n"
            f"Topic: {booking.topic}\n"
            f"Slot: {slot_txt}\n"
            f"Status: {booking.status.value}\n"
            f"Source: advisor_scheduler\n"
            f"Contact details will be collected separately through the secure link."
        )
    req = GmailDraftRequest(
        to=settings.advisor_email,
        subject=subject,
        body=body,
        approval_required=True,
        metadata={
            "booking_code": booking.code,
            "topic": booking.topic,
            "booking_status": booking.status.value,
            "source": "advisor_scheduler",
            "user_intent": user_intent,
        },
    )
    return gmail.create_draft(req)


def execute_reschedule_side_effects(
    *,
    settings: Settings,
    calendar: CalendarAdapter,
    sheets: SheetsAdapter,
    gmail: GmailAdapter,
    booking: Booking,
    new_slot: Slot,
    user_intent: str,
) -> OrchestrationResult:
    prev = booking.slot.label if booking.slot else None
    booking.previous_slot_label = prev
    booking.slot = new_slot
    booking.status = BookingStatus.RESCHEDULED

    title = f"Advisor Q&A — {booking.topic} — {booking.code}"
    meta = {
        "booking_code": booking.code,
        "topic": booking.topic,
        "booking_type": "reschedule",
        "source": "advisor_scheduler",
        "user_intent": user_intent,
    }
    start = new_slot.start
    end = start + timedelta(minutes=30)
    hold = CalendarHoldRequest(
        title=title,
        start_time=start,
        end_time=end,
        timezone="Asia/Kolkata",
        status=booking.status.value,
        metadata=meta,
    )
    if booking.calendar_hold_id:
        cres = calendar.update_hold(booking.calendar_hold_id, hold)
    else:
        cres = calendar.create_hold(hold)
    subject = f"Tentative Advisor Booking — {booking.topic} — {booking.code}"
    body = (
        f"Booking code: {booking.code}\n"
        f"Topic: {booking.topic}\n"
        f"Rescheduled slot: {new_slot.label}\n"
        f"Status: rescheduled\n"
        f"Source: advisor_scheduler\n"
        f"Contact details will be collected separately through the secure link."
    )
    greq = GmailDraftRequest(
        to=settings.advisor_email,
        subject=subject,
        body=body,
        approval_required=True,
        metadata={"booking_code": booking.code, "user_intent": user_intent},
    )
    gres = gmail.create_draft(greq)
    row = SheetsRow(
        created_at=_now(),
        updated_at=_now(),
        booking_code=booking.code,
        topic=booking.topic,
        intent=user_intent,
        requested_day=booking.requested_day,
        requested_time_window=booking.requested_time_window,
        confirmed_slot=new_slot.label,
        timezone="Asia/Kolkata",
        status=BookingStatus.RESCHEDULED.value,
        source="advisor_scheduler",
        calendar_hold_id=cres.external_id if cres.success else None,
        email_draft_id=gres.draft_id,
        previous_slot=prev,
        action_type="reschedule",
    )
    sres = sheets.append_row(row)

    partial = (not cres.success) or (not sres.success) or (not gres.success)
    ok = cres.success and sres.success and gres.success
    final = BookingStatus.FAILED if partial else BookingStatus.RESCHEDULED
    msg = "Your appointment has been rescheduled." if ok else (
        "Something went wrong while updating your booking. Please try again shortly."
    )
    return OrchestrationResult(
        success=ok,
        partial_failure=partial,
        user_message=msg,
        calendar_id=cres.external_id,
        sheet_row_id=sres.row_identifier,
        draft_id=gres.draft_id,
        final_status=final,
    )


def execute_cancel_side_effects(
    *,
    settings: Settings,
    calendar: CalendarAdapter,
    sheets: SheetsAdapter,
    gmail: GmailAdapter,
    booking: Booking,
    user_intent: str,
) -> OrchestrationResult:
    booking.status = BookingStatus.CANCELLED
    prior_cal_id = booking.calendar_hold_id
    if prior_cal_id:
        cres = calendar.delete_hold(prior_cal_id)
    else:
        cres = CalendarHoldResult(
            success=True,
            external_id=None,
            status="skipped",
            message=None,
        )
    subject = f"Cancelled Advisor Booking — {booking.topic} — {booking.code}"
    body = (
        f"Booking code: {booking.code}\n"
        f"Topic: {booking.topic}\n"
        f"Status: cancelled\n"
        f"Source: advisor_scheduler\n"
    )
    greq = GmailDraftRequest(
        to=settings.advisor_email,
        subject=subject,
        body=body,
        approval_required=True,
        metadata={"booking_code": booking.code, "user_intent": user_intent},
    )
    gres = gmail.create_draft(greq)
    row = SheetsRow(
        created_at=_now(),
        updated_at=_now(),
        booking_code=booking.code,
        topic=booking.topic,
        intent=user_intent,
        requested_day=booking.requested_day,
        requested_time_window=booking.requested_time_window,
        confirmed_slot=booking.slot.label if booking.slot else None,
        timezone="Asia/Kolkata",
        status=BookingStatus.CANCELLED.value,
        source="advisor_scheduler",
        calendar_hold_id=prior_cal_id,
        email_draft_id=gres.draft_id,
        action_type="cancel",
    )
    sres = sheets.append_row(row)
    partial = (not cres.success) or (not sres.success) or (not gres.success)
    ok = cres.success and sres.success and gres.success
    final = BookingStatus.FAILED if partial else BookingStatus.CANCELLED
    msg = "Your booking has been cancelled." if ok else (
        "Something went wrong while cancelling. Please try again shortly."
    )
    # After successful delete the event no longer exists; omit calendar id on success.
    cal_id_out: str | None
    if ok and prior_cal_id:
        cal_id_out = None
    else:
        cal_id_out = cres.external_id

    return OrchestrationResult(
        success=ok,
        partial_failure=partial,
        user_message=msg,
        calendar_id=cal_id_out,
        sheet_row_id=sres.row_identifier,
        draft_id=gres.draft_id,
        final_status=final,
    )
