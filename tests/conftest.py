import pytest

from advisor_scheduler.config import Settings
from advisor_scheduler.core.engine import ConversationEngine
from advisor_scheduler.integrations.google_workspace.stubs import CalendarStub, GmailStub, SheetsStub
from advisor_scheduler.llm import StubLlmClient
from advisor_scheduler.services.booking_service import BookingService
from advisor_scheduler.services.slot_service import SlotService
from advisor_scheduler.core.session import SessionStore


@pytest.fixture
def fixed_now():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
    return datetime(2026, 4, 17, 10, 0, tzinfo=IST)  # Friday


@pytest.fixture
def llm_stub() -> StubLlmClient:
    return StubLlmClient(responses=[])


@pytest.fixture
def engine(fixed_now, llm_stub) -> ConversationEngine:
    settings = Settings(
        secure_details_base_url="https://secure.nextleap.test/details",
        advisor_email="a@example.com",
        session_timeout_minutes=20,
        use_mcp=False,
    )
    return ConversationEngine(
        sessions=SessionStore(timeout_minutes=20),
        bookings=BookingService(),
        slots=SlotService(now_fn=lambda: fixed_now, settings=settings),
        settings=settings,
        calendar=CalendarStub(),
        sheets=SheetsStub(),
        gmail=GmailStub(),
        llm=llm_stub,
    )
