from __future__ import annotations

from typing import Protocol

from advisor_scheduler.integrations.google_workspace.stubs import (
    CalendarHoldRequest,
    CalendarHoldResult,
    GmailDraftRequest,
    GmailDraftResult,
    SheetsAppendResult,
    SheetsRow,
)


class CalendarAdapter(Protocol):
    def create_hold(self, req: CalendarHoldRequest) -> CalendarHoldResult: ...

    def update_hold(self, event_id: str, req: CalendarHoldRequest) -> CalendarHoldResult: ...

    def delete_hold(self, event_id: str) -> CalendarHoldResult: ...


class SheetsAdapter(Protocol):
    def append_row(self, row: SheetsRow) -> SheetsAppendResult: ...

    def list_rows(self) -> list[SheetsRow]: ...


class GmailAdapter(Protocol):
    def create_draft(self, req: GmailDraftRequest) -> GmailDraftResult: ...
