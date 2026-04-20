from __future__ import annotations

import json
from datetime import date

from advisor_scheduler.core.session import Session
from advisor_scheduler.core.topics import ALLOWED_TOPICS
from advisor_scheduler.intents.router import route_intent
from advisor_scheduler.llm.response_schema import ALLOWED_ACTIONS, ALLOWED_INTENTS, ALLOWED_STATES
from advisor_scheduler.types.models import Booking

SYSTEM_PROMPT = """You are NextLeap's advisor appointment scheduler for Phase 1 chat support.

Follow these rules exactly:
- This conversation is for informational support only and does not provide investment advice.
- Never ask for or collect phone number, email address, account number, PAN, Aadhaar, or similar identifiers.
- If the user asks for investment advice, refuse and redirect to booking or informational support.
- Only support these intents: book_new, reschedule, cancel, what_to_prepare, check_availability.
- Only support these topics: KYC / Onboarding, SIP / Mandates, Statements / Tax Docs, Withdrawals / Timelines, Account Changes / Nominee.
- All availability and confirmation wording must use IST.
- Before any booking, reschedule, cancel, or waitlist side effect, ask for explicit confirmation.
- Repeat the full selected date and time before final confirmation.
- Reschedule and cancel flows may ask only for booking code in NL-XXXX format.
- When collecting a booking code, if the user speaks a code without a hyphen (e.g. NLUW4G) or uses spoken punctuation (e.g. "NL hyphen UW4G"), normalize it to NL-XXXX format in the booking_code field.
- Keep replies concise, natural, and focused on one main question at a time.
- After a completed booking-related result, ask whether anything else is needed before closing.

You must respond with valid JSON only. Do not include markdown fences.
"""


def _booking_summary(booking: Booking | None) -> dict[str, object] | None:
    if not booking:
        return None
    return {
        "code": booking.code,
        "topic": booking.topic,
        "status": booking.status.value,
        "slot_label": booking.slot.label if booking.slot else None,
        "requested_day": booking.requested_day,
        "requested_time_window": booking.requested_time_window,
    }


def _session_payload(session: Session) -> dict[str, object]:
    preferred_date: str | None = None
    if isinstance(session.preferred_date, date):
        preferred_date = session.preferred_date.isoformat()

    offered_slots = [slot.label for slot in session.offered_slots]
    return {
        "state": session.state,
        "active_intent": session.active_intent,
        "topic": session.topic,
        "requested_day_label": session.requested_day_label,
        "preferred_date": preferred_date,
        "time_window": session.time_window,
        "offered_slots": offered_slots,
        "pending_slot": session.pending_slot.label if session.pending_slot else None,
        "last_availability_windows": session.last_availability_windows[:2],
        "active_booking": _booking_summary(session.active_booking),
        "target_booking": _booking_summary(session.target_booking),
        "waitlist_topic": session.waitlist_topic,
        "waitlist_day_label": session.waitlist_day_label,
        "code_retries": session.code_retries,
        "awaiting_confirmation_action": session.awaiting_confirmation_action,
        "last_action": session.last_action,
        "last_error": session.last_error,
    }


_COMPACT_SCHEMA = (
    '{"reply":"str","next_state":"' + "|".join(ALLOWED_STATES) + '",'
    '"intent":"' + "|".join(ALLOWED_INTENTS) + '",'
    '"action":"' + "|".join(ALLOWED_ACTIONS) + '",'
    '"topic":"allowed topic|null","requested_day_text":"str|null","resolved_day_iso":"YYYY-MM-DD|null",'
    '"time_window":"morning|afternoon|evening|null",'
    '"selected_slot_index":"0|1|null","booking_code":"NL-XXXX|null",'
    '"needs_clarification":false,"asks_for_confirmation":false,"close_session":false}'
)

_DAY_RESOLUTION_SCHEMA = (
    '{"resolved_date_iso":"YYYY-MM-DD|null","is_ambiguous":false,'
    '"reason":"str|null","normalized_time_window":"morning|afternoon|evening|null"}'
)


def build_gemini_prompt(session: Session, user_message: str) -> str:
    sig = route_intent(user_message)
    payload = {
        "topics": list(ALLOWED_TOPICS),
        "hint": {"intent": sig.intent.value, "conf": round(sig.confidence, 2)},
        "s": _session_payload(session),
        "history": session.history[-4:],
    }
    return f"{SYSTEM_PROMPT}\nSchema: {_COMPACT_SCHEMA}\n\nContext:\n{json.dumps(payload, separators=(',', ':'))}"


def build_day_resolution_prompt(session: Session, user_message: str) -> str:
    payload = {
        "state": session.state,
        "topic": session.topic,
        "requested_day_label": session.requested_day_label,
        "preferred_date": session.preferred_date.isoformat() if isinstance(session.preferred_date, date) else None,
        "history": session.history[-4:],
        "user_message": user_message,
    }
    instructions = """Return only JSON.

Task:
- Resolve the user's intended calendar day in Asia/Kolkata (IST) from the latest user message and context.
- Set resolved_date_iso to exactly one date as YYYY-MM-DD when a single day is intended (per IST).
- Set is_ambiguous to true when multiple days are plausible, the phrasing is vague ("sometime next week"), or a boundary is unclear.
- When is_ambiguous is true, resolved_date_iso must be null. Optionally set reason to a short machine-readable hint.
- normalized_time_window may be set to morning, afternoon, or evening only when clearly stated; otherwise null.
- Do not echo free-form day phrases; the app consumes the ISO date only.
"""
    return (
        f"{SYSTEM_PROMPT}\n"
        f"{instructions}\n"
        f"Schema: {_DAY_RESOLUTION_SCHEMA}\n\n"
        f"Context:\n{json.dumps(payload, separators=(',', ':'))}"
    )
