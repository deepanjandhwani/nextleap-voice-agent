"""Unit tests for heuristic intent routing (deterministic, no LLM)."""

import pytest

from advisor_scheduler.intents.router import route_intent
from advisor_scheduler.types.models import Intent


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("What all do I need to be prepared with?", Intent.WHAT_TO_PREPARE),
        ("what should I have ready for the call?", Intent.WHAT_TO_PREPARE),
        ("I want to be prepared — what do I need?", Intent.WHAT_TO_PREPARE),
        ("get ready checklist for SIP", Intent.WHAT_TO_PREPARE),
        ("prepared with documents for KYC", Intent.WHAT_TO_PREPARE),
        ("I'm prepared to book an appointment", Intent.BOOK_NEW),
        ("I want to book", Intent.BOOK_NEW),
        ("reschedule my slot", Intent.RESCHEDULE),
        ("cancel my appointment", Intent.CANCEL),
    ],
)
def test_route_intent_prepare_and_neighbors(message: str, expected: Intent) -> None:
    assert route_intent(message).intent == expected
