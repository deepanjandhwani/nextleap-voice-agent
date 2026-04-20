from __future__ import annotations

from advisor_scheduler.config import Settings
from advisor_scheduler.integrations.google_workspace.stubs import CalendarStub, GmailStub, SheetsStub
from advisor_scheduler.integrations.protocols import CalendarAdapter, GmailAdapter, SheetsAdapter


def build_adapters(settings: Settings) -> tuple[CalendarAdapter, SheetsAdapter, GmailAdapter]:
    if not settings.use_mcp:
        return CalendarStub(), SheetsStub(), GmailStub()
    from advisor_scheduler.integrations.google_workspace.mcp import (
        CalendarMcpAdapter,
        GmailMcpAdapter,
        SheetsMcpAdapter,
        load_mcp_client_source,
    )

    src = load_mcp_client_source(settings)
    return (
        CalendarMcpAdapter(settings, src),
        SheetsMcpAdapter(settings, src),
        GmailMcpAdapter(settings, src),
    )
