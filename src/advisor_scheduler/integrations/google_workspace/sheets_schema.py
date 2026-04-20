"""Column order for the append-only pre-booking sheet (A through P).

Runtime writes, operator headers, and MCP docs must stay aligned on this order.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from advisor_scheduler.integrations.google_workspace.stubs import SheetsRow

# Row 1 is reserved for human-readable headers (recommended).
SHEETS_LOG_HEADERS: tuple[str, ...] = (
    "created_at",
    "updated_at",
    "booking_code",
    "topic",
    "intent",
    "requested_day",
    "requested_time_window",
    "confirmed_slot",
    "timezone",
    "status",
    "source",
    "notes",
    "calendar_hold_id",
    "email_draft_id",
    "previous_slot",
    "action_type",
)

SHEETS_LOG_COLUMN_COUNT = len(SHEETS_LOG_HEADERS)


def sheets_log_write_range(*, sheet: str, start_row: int, row_count: int) -> str:
    """A1 range covering ``row_count`` rows starting at ``start_row`` (16 columns)."""
    if row_count < 1:
        raise ValueError("row_count must be at least 1")
    end_row = start_row + row_count - 1
    return f"{sheet}!A{start_row}:P{end_row}"


def sheets_row_to_cell_strings(row: SheetsRow) -> list[str]:
    """One log row as strings, columns A–P, matching :data:`SHEETS_LOG_HEADERS`."""

    def _cell(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)

    cells = [
        _cell(row.created_at),
        _cell(row.updated_at),
        _cell(row.booking_code),
        _cell(row.topic),
        _cell(row.intent),
        _cell(row.requested_day),
        _cell(row.requested_time_window),
        _cell(row.confirmed_slot),
        _cell(row.timezone),
        _cell(row.status),
        _cell(row.source),
        _cell(row.notes),
        _cell(row.calendar_hold_id),
        _cell(row.email_draft_id),
        _cell(row.previous_slot),
        _cell(row.action_type),
    ]
    if len(cells) != SHEETS_LOG_COLUMN_COUNT:
        raise ValueError("SheetsRow field count drifted from SHEETS_LOG_HEADERS")
    return cells


def sheet_values_to_row(values: list[Any]) -> SheetsRow:
    normalized = ["" if v is None else str(v) for v in values[:SHEETS_LOG_COLUMN_COUNT]]
    if len(normalized) < SHEETS_LOG_COLUMN_COUNT:
        normalized.extend([""] * (SHEETS_LOG_COLUMN_COUNT - len(normalized)))

    def _dt(idx: int) -> datetime:
        raw = normalized[idx].strip()
        if not raw:
            raise ValueError("missing datetime cell")
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))

    def _text(idx: int) -> str | None:
        raw = normalized[idx].strip()
        return raw or None

    return SheetsRow(
        created_at=_dt(0),
        updated_at=_dt(1),
        booking_code=normalized[2].strip(),
        topic=normalized[3].strip(),
        intent=normalized[4].strip(),
        requested_day=_text(5),
        requested_time_window=_text(6),
        confirmed_slot=_text(7),
        timezone=normalized[8].strip() or "Asia/Kolkata",
        status=normalized[9].strip(),
        source=normalized[10].strip(),
        notes=_text(11),
        calendar_hold_id=_text(12),
        email_draft_id=_text(13),
        previous_slot=_text(14),
        action_type=_text(15),
    )
