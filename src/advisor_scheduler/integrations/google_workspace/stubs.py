from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CalendarHoldRequest:
    title: str
    start_time: datetime
    end_time: datetime
    timezone: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CalendarHoldResult:
    success: bool
    external_id: str | None
    status: str
    message: str | None = None


@dataclass
class SheetsRow:
    created_at: datetime
    updated_at: datetime
    booking_code: str
    topic: str
    intent: str
    requested_day: str | None
    requested_time_window: str | None
    confirmed_slot: str | None
    timezone: str
    status: str
    source: str
    notes: str | None = None
    calendar_hold_id: str | None = None
    email_draft_id: str | None = None
    previous_slot: str | None = None
    action_type: str | None = None


@dataclass
class SheetsAppendResult:
    success: bool
    row_identifier: str | None
    status: str
    message: str | None = None


@dataclass
class GmailDraftRequest:
    to: str
    subject: str
    body: str
    approval_required: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GmailDraftResult:
    success: bool
    draft_id: str | None
    status: str
    message: str | None = None


class CalendarStub:
    def __init__(self) -> None:
        self.calls: list[CalendarHoldRequest] = []
        self.update_calls: list[tuple[str, CalendarHoldRequest]] = []
        self.delete_calls: list[str] = []
        self.fail_next = False

    def create_hold(self, req: CalendarHoldRequest) -> CalendarHoldResult:
        self.calls.append(req)
        if self.fail_next:
            self.fail_next = False
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message="calendar_stub_failure",
            )
        return CalendarHoldResult(
            success=True,
            external_id=f"cal-{len(self.calls)}",
            status=req.status,
            message=None,
        )

    def update_hold(self, event_id: str, req: CalendarHoldRequest) -> CalendarHoldResult:
        self.update_calls.append((event_id, req))
        if self.fail_next:
            self.fail_next = False
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message="calendar_stub_failure",
            )
        return CalendarHoldResult(
            success=True,
            external_id=event_id,
            status=req.status,
            message=None,
        )

    def delete_hold(self, event_id: str) -> CalendarHoldResult:
        self.delete_calls.append(event_id)
        if self.fail_next:
            self.fail_next = False
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message="calendar_stub_failure",
            )
        return CalendarHoldResult(success=True, external_id=event_id, status="deleted", message=None)


class SheetsStub:
    def __init__(self) -> None:
        self.rows: list[SheetsRow] = []
        self.fail_next = False

    def append_row(self, row: SheetsRow) -> SheetsAppendResult:
        if self.fail_next:
            self.fail_next = False
            return SheetsAppendResult(
                success=False,
                row_identifier=None,
                status="failed",
                message="sheets_stub_failure",
            )
        self.rows.append(row)
        rid = f"row-{len(self.rows)}"
        return SheetsAppendResult(success=True, row_identifier=rid, status=row.status, message=None)

    def list_rows(self) -> list[SheetsRow]:
        return list(self.rows)


class GmailStub:
    def __init__(self) -> None:
        self.drafts: list[GmailDraftRequest] = []
        self.fail_next = False

    def create_draft(self, req: GmailDraftRequest) -> GmailDraftResult:
        self.drafts.append(req)
        if self.fail_next:
            self.fail_next = False
            return GmailDraftResult(
                success=False,
                draft_id=None,
                status="failed",
                message="gmail_stub_failure",
            )
        return GmailDraftResult(
            success=True,
            draft_id=f"draft-{len(self.drafts)}",
            status="draft",
            message=None,
        )
