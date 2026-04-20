"""In-repo FastMCP server for Google Calendar, Sheets, and Gmail.

This replaces the previous trio of external Node MCP servers
(``@cocal/google-calendar-mcp``, ``mcp-google-sheets``, and
``@shinzolabs/gmail-mcp``). Running the full surface from one Python
process keeps transport handling in stdio mode, avoids the Gmail
``PORT``-into-HTTP-mode failure, and pins a stable, narrow tool
contract that the orchestrator already expects.

Tools:
    - calendar_create_hold
    - calendar_update_hold
    - calendar_delete_hold
    - calendar_get_freebusy
    - sheets_append_prebooking (next free row; explicit ``A{row}:P{row}`` write)
    - sheets_list_prebookings
    - gmail_create_draft

Run as a stdio MCP server::

    python -m advisor_scheduler.integrations.google_workspace.server
"""

from __future__ import annotations

import base64
import logging
from email.mime.text import MIMEText
from typing import Any

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from advisor_scheduler.integrations.google_workspace import google_clients
from advisor_scheduler.integrations.google_workspace.sheets_schema import (
    SHEETS_LOG_COLUMN_COUNT,
    SHEETS_LOG_HEADERS,
    sheets_log_write_range,
)

logger = logging.getLogger("advisor_google_mcp")

mcp = FastMCP("advisor-google-workspace")


def _http_error_message(exc: HttpError) -> str:
    try:
        payload = exc.error_details  # type: ignore[attr-defined]
        if payload:
            return str(payload)
    except AttributeError:
        pass
    return str(exc)


@mcp.tool()
def calendar_create_hold(
    calendar_id: str,
    title: str,
    start: str,
    end: str,
    time_zone: str = "Asia/Kolkata",
    description: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Create a Google Calendar event used as an advisor hold.

    Times are RFC3339 strings; ``status`` accepts Google Calendar values
    (``confirmed``, ``tentative``, or ``cancelled``).
    """
    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": start, "timeZone": time_zone},
        "end": {"dateTime": end, "timeZone": time_zone},
    }
    if description:
        body["description"] = description
    if status:
        body["status"] = status
    try:
        service = google_clients.calendar_service()
        event = service.events().insert(calendarId=calendar_id, body=body).execute()
    except HttpError as exc:
        return {"error": "calendar_insert_failed", "message": _http_error_message(exc)}
    return {
        "id": event.get("id"),
        "status": event.get("status"),
        "html_link": event.get("htmlLink"),
    }


@mcp.tool()
def calendar_update_hold(
    calendar_id: str,
    event_id: str,
    title: str,
    start: str,
    end: str,
    time_zone: str = "Asia/Kolkata",
    description: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Patch an existing calendar event (reschedule or change status)."""
    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": start, "timeZone": time_zone},
        "end": {"dateTime": end, "timeZone": time_zone},
    }
    if description:
        body["description"] = description
    if status:
        body["status"] = status
    try:
        service = google_clients.calendar_service()
        event = (
            service.events()
            .patch(calendarId=calendar_id, eventId=event_id, body=body)
            .execute()
        )
    except HttpError as exc:
        return {"error": "calendar_patch_failed", "message": _http_error_message(exc)}
    return {
        "id": event.get("id"),
        "status": event.get("status"),
        "html_link": event.get("htmlLink"),
    }


@mcp.tool()
def calendar_delete_hold(calendar_id: str, event_id: str) -> dict[str, Any]:
    """Delete a calendar event (e.g. after cancellation)."""
    try:
        service = google_clients.calendar_service()
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as exc:
        return {"error": "calendar_delete_failed", "message": _http_error_message(exc)}
    return {"deleted": True, "event_id": event_id}


@mcp.tool()
def calendar_get_freebusy(
    calendar_id: str,
    time_min: str,
    time_max: str,
    time_zone: str = "Asia/Kolkata",
) -> dict[str, Any]:
    """Return ``busy`` intervals for ``calendar_id`` between ``time_min`` and ``time_max``."""
    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "timeZone": time_zone,
        "items": [{"id": calendar_id}],
    }
    try:
        service = google_clients.calendar_service()
        result = service.freebusy().query(body=body).execute()
    except HttpError as exc:
        return {"error": "freebusy_failed", "message": _http_error_message(exc)}

    calendars = result.get("calendars") or {}
    block = calendars.get(calendar_id) or {}
    return {"busy": block.get("busy") or []}


@mcp.tool()
def sheets_append_prebooking(
    spreadsheet_id: str,
    sheet: str,
    values: list[list[Any]],
) -> dict[str, Any]:
    """Write one or more log rows to the next free rows in columns A–P.

    Uses an explicit ``A{row}:P{row}`` range so placement does not depend
    on broad ``A:Z`` append heuristics. Latest row per ``booking_code``
    remains the current state (append-only event log).
    """
    if not values:
        return {"error": "no_values", "message": "values must contain at least one row"}
    normalized: list[list[str]] = []
    for row in values:
        cells = ["" if c is None else str(c) for c in row]
        if len(cells) < SHEETS_LOG_COLUMN_COUNT:
            cells.extend([""] * (SHEETS_LOG_COLUMN_COUNT - len(cells)))
        elif len(cells) > SHEETS_LOG_COLUMN_COUNT:
            cells = cells[:SHEETS_LOG_COLUMN_COUNT]
        normalized.append(cells)
    range_col_a = f"{sheet}!A:A"
    try:
        service = google_clients.sheets_service()
        col_a = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_col_a)
            .execute()
        )
        existing = col_a.get("values") or []
        next_row = len(existing) + 1
        if not existing:
            # Empty tab: reserve row 1 for schema headers so the first log row is row 2.
            rows_to_write: list[list[str]] = [list(SHEETS_LOG_HEADERS)] + normalized
            write_start_row = 1
        else:
            rows_to_write = normalized
            write_start_row = next_row
        write_range = sheets_log_write_range(
            sheet=sheet, start_row=write_start_row, row_count=len(rows_to_write)
        )
        result = (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=write_range,
                valueInputOption="USER_ENTERED",
                body={"values": rows_to_write},
            )
            .execute()
        )
    except HttpError as exc:
        return {"error": "sheets_write_failed", "message": _http_error_message(exc)}
    return {
        "updated_range": result.get("updatedRange"),
        "updated_rows": len(rows_to_write),
        "spreadsheet_id": spreadsheet_id,
    }


@mcp.tool()
def sheets_list_prebookings(
    spreadsheet_id: str,
    sheet: str,
) -> dict[str, Any]:
    """Return append-only pre-booking rows from columns A-P.

    Row 1 may contain headers and is skipped automatically when it matches
    the documented runtime schema.
    """
    try:
        service = google_clients.sheets_service()
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{sheet}!A:P")
            .execute()
        )
    except HttpError as exc:
        return {"error": "sheets_read_failed", "message": _http_error_message(exc)}
    rows = result.get("values") or []
    if rows and rows[0] and str(rows[0][0]).strip() == "created_at":
        rows = rows[1:]
    return {"rows": rows, "spreadsheet_id": spreadsheet_id}


@mcp.tool()
def gmail_create_draft(
    to: str,
    subject: str,
    body: str,
    sender: str | None = None,
) -> dict[str, Any]:
    """Create a Gmail draft. Never sends; Phase 1 is draft-only.

    ``sender`` is read from server-side configuration; the agent never
    forwards user-supplied emails into this tool.
    """
    message = MIMEText(body)
    message["To"] = to
    message["Subject"] = subject
    if sender:
        message["From"] = sender
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    try:
        service = google_clients.gmail_service()
        draft = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw}})
            .execute()
        )
    except HttpError as exc:
        return {"error": "gmail_draft_failed", "message": _http_error_message(exc)}

    msg = draft.get("message") or {}
    return {
        "id": draft.get("id"),
        "message_id": msg.get("id"),
        "thread_id": msg.get("threadId"),
    }


def main() -> None:
    """Entry point used by the ``advisor-google-mcp`` console script and CLI runs."""
    logging.basicConfig(level=logging.INFO)
    mcp.run()


if __name__ == "__main__":
    main()
