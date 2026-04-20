from __future__ import annotations

import re
from dataclasses import dataclass

from advisor_scheduler.types.models import Intent


@dataclass(frozen=True)
class IntentSignal:
    intent: Intent
    confidence: float


_BOOK = re.compile(
    r"\b(book|schedule|appointment|reserve|slot|meeting)\b",
    re.I,
)
_RESCHEDULE = re.compile(r"\b(reschedule|move (my )?appointment|change (my )?slot)\b", re.I)
_CANCEL = re.compile(r"\b(cancel|cancellation)\b", re.I)
# Covers natural variants ("prepared with", "be prepared", "have ready") so users are not
# dropped to UNKNOWN/LLM and misrouted to reschedule/code prompts. Exclude "prepared to book"
# so obvious booking intent still matches _BOOK.
_PREPARE = re.compile(
    r"(?:"
    r"\b(?:"
    r"prepare|what to (?:bring|prepare|have ready)|documents?|checklist|"
    r"be prepared|prepared with|have ready|get ready"
    r")\b|"
    r"\bprepared\b(?!\s+to\s+book)"
    r")",
    re.I,
)
_AVAIL = re.compile(
    r"\b(availability|available|open slots|when are you|openings?)\b",
    re.I,
)


def route_intent(message: str) -> IntentSignal:
    """
    Lightweight heuristic classifier for supported intents.
    """
    t = message.strip()
    if not t:
        return IntentSignal(Intent.UNKNOWN, 0.0)

    lower = t.lower()
    if lower.strip() in {
        "cancel that",
        "cancel it",
        "never mind",
        "forget it",
        "not now",
        "skip",
    }:
        return IntentSignal(Intent.UNKNOWN, 0.2)
    if _RESCHEDULE.search(lower):
        return IntentSignal(Intent.RESCHEDULE, 0.9)
    if _CANCEL.search(lower):
        return IntentSignal(Intent.CANCEL, 0.85)
    if _PREPARE.search(lower):
        return IntentSignal(Intent.WHAT_TO_PREPARE, 0.75)
    if _AVAIL.search(lower):
        return IntentSignal(Intent.CHECK_AVAILABILITY, 0.75)
    if _BOOK.search(lower):
        return IntentSignal(Intent.BOOK_NEW, 0.8)

    # weak defaults when user is still vague
    if "help" in lower and len(lower) < 40:
        return IntentSignal(Intent.UNKNOWN, 0.3)

    return IntentSignal(Intent.UNKNOWN, 0.2)


def extract_booking_code(message: str) -> str | None:
    m = re.search(r"\b(NL-[A-Z0-9]{4})\b", message.upper())
    return m.group(1) if m else None


def parse_booking_code(message: str) -> str | None:
    """Same as extract_booking_code, plus a fallback when the message is only NL-XXXX."""
    code = extract_booking_code(message)
    if code:
        return code
    stripped = message.strip().upper()
    if re.match(r"^NL-[A-Z0-9]{4}$", stripped):
        return stripped
    return None
