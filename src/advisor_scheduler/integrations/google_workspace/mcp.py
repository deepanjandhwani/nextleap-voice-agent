"""FastMCP-backed adapters that target the in-repo Python FastMCP server.

The adapters speak a narrow tool contract (see
:mod:`advisor_scheduler.integrations.google_workspace.server`) so the
orchestrator does not depend on raw Google API shapes. The same client
source is reused for Calendar, Sheets, and Gmail because all three live
in the single in-repo server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from advisor_scheduler.config import Settings
from advisor_scheduler.integrations.google_workspace.sheets_schema import (
    sheet_values_to_row,
    sheets_row_to_cell_strings,
)
from advisor_scheduler.integrations.google_workspace.stubs import (
    CalendarHoldRequest,
    CalendarHoldResult,
    GmailDraftRequest,
    GmailDraftResult,
    SheetsAppendResult,
    SheetsRow,
)
from advisor_scheduler.types.models import Slot

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)


def default_in_repo_mcp_command() -> dict[str, Any]:
    """FastMCP ``mcpServers`` entry that launches the in-repo Python server."""
    import sys

    return {
        "mcpServers": {
            "advisor-google-workspace": {
                "command": sys.executable,
                "args": ["-m", "advisor_scheduler.integrations.google_workspace.server"],
                "env": {},
            }
        }
    }


def load_mcp_client_source(settings: Settings) -> Any:
    """Resolve the FastMCP Client transport.

    Accepts a JSON file path, inline JSON, or a stdio command string.
    When ``mcp_google_config`` is unset, falls back to launching the
    in-repo Python FastMCP server module.
    """
    raw = settings.mcp_google_config
    if not raw:
        return default_in_repo_mcp_command()
    stripped = raw.strip()
    path = Path(stripped)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    return stripped


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - propagate exactly
            error["value"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")


def _exception_message(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "mcp_call_timeout"
    return str(exc)


async def _call_tool_with_timeout(
    settings: Settings,
    client_source: Any,
    tool_name: str,
    arguments: dict[str, Any] | None,
) -> Any:
    started = perf_counter()
    try:
        return await asyncio.wait_for(
            _call_tool_async(client_source, tool_name, arguments),
            timeout=settings.mcp_call_timeout_seconds,
        )
    except TimeoutError:
        elapsed_ms = (perf_counter() - started) * 1000
        logger.warning(
            "MCP tool timed out: tool=%s timeout_s=%.1f elapsed_ms=%.1f",
            tool_name,
            settings.mcp_call_timeout_seconds,
            elapsed_ms,
        )
        raise
    except Exception:
        elapsed_ms = (perf_counter() - started) * 1000
        logger.exception(
            "MCP tool failed: tool=%s elapsed_ms=%.1f",
            tool_name,
            elapsed_ms,
        )
        raise


async def _list_mcp_tool_names_async(client_source: Any) -> list[str]:
    from fastmcp import Client

    async with Client(client_source) as client:
        tools = await client.list_tools()
    return [t.name for t in tools]


def list_mcp_tool_names(client_source: Any) -> list[str]:
    """Return tool names from the configured MCP server."""
    return _run_async(_list_mcp_tool_names_async(client_source))


async def _call_tool_async(client_source: Any, tool_name: str, arguments: dict[str, Any] | None) -> Any:
    from fastmcp import Client

    async with Client(client_source) as client:
        return await client.call_tool(tool_name, arguments or {}, raise_on_error=False)


def _tool_payload(result: Any) -> Any:
    from fastmcp.client.client import CallToolResult

    if not isinstance(result, CallToolResult):
        return None
    if result.is_error:
        return None
    if result.data is not None:
        return result.data
    if result.structured_content:
        return result.structured_content
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            text = text.strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return None


def _is_error_payload(payload: Any) -> str | None:
    if isinstance(payload, dict) and "error" in payload:
        return str(payload.get("message") or payload.get("error"))
    return None


def _extract_event_id(payload: Any) -> str | None:
    if isinstance(payload, dict) and payload.get("id"):
        return str(payload["id"])
    return None


def _extract_row_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for k in ("updated_range", "updatedRange", "spreadsheet_id", "spreadsheetId"):
            v = payload.get(k)
            if v:
                return str(v)
    return None


def _extract_draft_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for k in ("id", "draft_id", "draftId"):
            v = payload.get(k)
            if v:
                return str(v)
    return None


def _parse_iso_dt(value: str) -> datetime:
    v = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def _extract_busy_intervals(payload: Any) -> list[tuple[datetime, datetime]]:
    """Normalize free/busy responses to IST intervals.

    Accepts both the in-repo server's ``{"busy": [...]}`` shape and the
    raw Google Calendar ``{"calendars": {id: {"busy": [...]}}`` shape.
    """
    if payload is None:
        return []
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    busy: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("busy"), list):
            busy = [b for b in payload["busy"] if isinstance(b, dict)]
        elif "calendars" in payload:
            for _cid, block in payload["calendars"].items():
                if isinstance(block, dict) and isinstance(block.get("busy"), list):
                    busy.extend([b for b in block["busy"] if isinstance(b, dict)])
    out: list[tuple[datetime, datetime]] = []
    for b in busy:
        start = b.get("start")
        end = b.get("end")
        if not start or not end:
            continue
        try:
            out.append((_parse_iso_dt(str(start)), _parse_iso_dt(str(end))))
        except ValueError:
            continue
    return out


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x[0])
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        ps, pe = merged[-1]
        if start <= pe:
            merged[-1] = (ps, max(pe, end))
        else:
            merged.append((start, end))
    return merged


def _slot_overlaps_busy(slot_start: datetime, slot_end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    for bs, be in busy:
        if slot_start < be and bs < slot_end:
            return True
    return False


def _window_allows(window: str | None, hour: int) -> bool:
    if window is None:
        return True
    if window == "morning":
        return 9 <= hour < 12
    if window == "afternoon":
        return 12 <= hour < 17
    if window == "evening":
        return 17 <= hour < 21
    return True


def _format_ist(dt: datetime) -> str:
    return dt.strftime("%A, %d %b %Y at %H:%M IST")


def _calendar_status(status: str | None) -> str | None:
    """Map our internal status to a Google Calendar event status."""
    if status is None:
        return None
    s = status.lower()
    if s == "tentative":
        return "tentative"
    if s == "cancelled":
        return "cancelled"
    return "confirmed"


class CalendarMcpAdapter:
    def __init__(self, settings: Settings, client_source: Any) -> None:
        self._settings = settings
        self._client_source = client_source

    def create_hold(self, req: CalendarHoldRequest) -> CalendarHoldResult:
        cal_id = self._settings.google_calendar_id
        if not cal_id:
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message="google_calendar_id is not configured",
            )
        start_s = req.start_time.astimezone(IST).isoformat()
        end_s = req.end_time.astimezone(IST).isoformat()
        args: dict[str, Any] = {
            "calendar_id": cal_id,
            "title": req.title,
            "start": start_s,
            "end": end_s,
            "time_zone": req.timezone or "Asia/Kolkata",
        }
        if req.metadata:
            args["description"] = json.dumps(req.metadata, ensure_ascii=False)
        cal_status = _calendar_status(req.status)
        if cal_status:
            args["status"] = cal_status

        tool = self._settings.mcp_tool_calendar_create_hold

        async def _go():
            return await _call_tool_with_timeout(self._settings, self._client_source, tool, args)

        try:
            raw = _run_async(_go())
            from fastmcp.client.client import CallToolResult as CTR

            if isinstance(raw, CTR) and raw.is_error:
                return CalendarHoldResult(
                    success=False,
                    external_id=None,
                    status="failed",
                    message="mcp_calendar_tool_error",
                )
            payload = _tool_payload(raw)
            err = _is_error_payload(payload)
            if err:
                return CalendarHoldResult(
                    success=False, external_id=None, status="failed", message=err
                )
            eid = _extract_event_id(payload)
            if eid:
                return CalendarHoldResult(success=True, external_id=eid, status=req.status, message=None)
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message="mcp_calendar_unexpected_response",
            )
        except Exception as exc:  # noqa: BLE001 — surface as adapter failure
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message=_exception_message(exc),
            )

    def update_hold(self, event_id: str, req: CalendarHoldRequest) -> CalendarHoldResult:
        cal_id = self._settings.google_calendar_id
        if not cal_id:
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message="google_calendar_id is not configured",
            )
        start_s = req.start_time.astimezone(IST).isoformat()
        end_s = req.end_time.astimezone(IST).isoformat()
        args: dict[str, Any] = {
            "calendar_id": cal_id,
            "event_id": event_id,
            "title": req.title,
            "start": start_s,
            "end": end_s,
            "time_zone": req.timezone or "Asia/Kolkata",
        }
        if req.metadata:
            args["description"] = json.dumps(req.metadata, ensure_ascii=False)
        cal_status = _calendar_status(req.status)
        if cal_status:
            args["status"] = cal_status

        tool = self._settings.mcp_tool_calendar_update_hold

        async def _go():
            return await _call_tool_with_timeout(self._settings, self._client_source, tool, args)

        try:
            raw = _run_async(_go())
            from fastmcp.client.client import CallToolResult as CTR

            if isinstance(raw, CTR) and raw.is_error:
                return CalendarHoldResult(
                    success=False,
                    external_id=None,
                    status="failed",
                    message="mcp_calendar_tool_error",
                )
            payload = _tool_payload(raw)
            err = _is_error_payload(payload)
            if err:
                return CalendarHoldResult(
                    success=False, external_id=None, status="failed", message=err
                )
            eid = _extract_event_id(payload)
            if eid:
                return CalendarHoldResult(success=True, external_id=eid, status=req.status, message=None)
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message="mcp_calendar_unexpected_response",
            )
        except Exception as exc:  # noqa: BLE001
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message=_exception_message(exc),
            )

    def delete_hold(self, event_id: str) -> CalendarHoldResult:
        cal_id = self._settings.google_calendar_id
        if not cal_id:
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message="google_calendar_id is not configured",
            )
        args: dict[str, Any] = {"calendar_id": cal_id, "event_id": event_id}
        tool = self._settings.mcp_tool_calendar_delete_hold

        async def _go():
            return await _call_tool_with_timeout(self._settings, self._client_source, tool, args)

        try:
            raw = _run_async(_go())
            from fastmcp.client.client import CallToolResult as CTR

            if isinstance(raw, CTR) and raw.is_error:
                return CalendarHoldResult(
                    success=False,
                    external_id=None,
                    status="failed",
                    message="mcp_calendar_tool_error",
                )
            payload = _tool_payload(raw)
            err = _is_error_payload(payload)
            if err:
                return CalendarHoldResult(
                    success=False, external_id=None, status="failed", message=err
                )
            if isinstance(payload, dict) and payload.get("deleted") is True:
                eid = str(payload.get("event_id") or event_id)
                return CalendarHoldResult(success=True, external_id=eid, status="deleted", message=None)
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message="mcp_calendar_unexpected_response",
            )
        except Exception as exc:  # noqa: BLE001
            return CalendarHoldResult(
                success=False,
                external_id=None,
                status="failed",
                message=_exception_message(exc),
            )


class SheetsMcpAdapter:
    def __init__(self, settings: Settings, client_source: Any) -> None:
        self._settings = settings
        self._client_source = client_source

    def append_row(self, row: SheetsRow) -> SheetsAppendResult:
        sid = self._settings.google_sheets_spreadsheet_id
        tab = self._settings.google_sheets_tab
        if not sid:
            return SheetsAppendResult(
                success=False,
                row_identifier=None,
                status="failed",
                message="google_sheets_spreadsheet_id is not configured",
            )

        values = [sheets_row_to_cell_strings(row)]

        tool = self._settings.mcp_tool_sheets_append_prebooking
        args = {
            "spreadsheet_id": sid,
            "sheet": tab,
            "values": values,
        }

        async def _go():
            return await _call_tool_with_timeout(self._settings, self._client_source, tool, args)

        try:
            raw = _run_async(_go())
            from fastmcp.client.client import CallToolResult as CTR

            if isinstance(raw, CTR) and raw.is_error:
                return SheetsAppendResult(
                    success=False,
                    row_identifier=None,
                    status="failed",
                    message="mcp_sheets_tool_error",
                )
            payload = _tool_payload(raw)
            err = _is_error_payload(payload)
            if err:
                return SheetsAppendResult(
                    success=False, row_identifier=None, status="failed", message=err
                )
            rid = _extract_row_id(payload)
            return SheetsAppendResult(
                success=True,
                row_identifier=rid or "appended",
                status=row.status,
                message=None,
            )
        except Exception as exc:  # noqa: BLE001
            return SheetsAppendResult(
                success=False,
                row_identifier=None,
                status="failed",
                message=_exception_message(exc),
            )

    def list_rows(self) -> list[SheetsRow]:
        sid = self._settings.google_sheets_spreadsheet_id
        tab = self._settings.google_sheets_tab
        if not sid:
            return []

        tool = self._settings.mcp_tool_sheets_list_prebookings
        args = {"spreadsheet_id": sid, "sheet": tab}

        async def _go():
            return await _call_tool_with_timeout(self._settings, self._client_source, tool, args)

        try:
            raw = _run_async(_go())
            from fastmcp.client.client import CallToolResult as CTR

            if isinstance(raw, CTR) and raw.is_error:
                return []
            payload = _tool_payload(raw)
            err = _is_error_payload(payload)
            if err:
                return []
            rows_payload = payload.get("rows") if isinstance(payload, dict) else None
            if not isinstance(rows_payload, list):
                return []
            rows: list[SheetsRow] = []
            for raw_row in rows_payload:
                if not isinstance(raw_row, list):
                    continue
                try:
                    rows.append(sheet_values_to_row(raw_row))
                except (TypeError, ValueError):
                    continue
            return rows
        except Exception:
            return []


class GmailMcpAdapter:
    def __init__(self, settings: Settings, client_source: Any) -> None:
        self._settings = settings
        self._client_source = client_source

    def create_draft(self, req: GmailDraftRequest) -> GmailDraftResult:
        tool = self._settings.mcp_tool_gmail_create_draft
        args: dict[str, Any] = {
            "to": req.to,
            "subject": req.subject,
            "body": req.body,
        }

        async def _go():
            return await _call_tool_with_timeout(self._settings, self._client_source, tool, args)

        try:
            raw = _run_async(_go())
            from fastmcp.client.client import CallToolResult as CTR

            if isinstance(raw, CTR) and raw.is_error:
                return GmailDraftResult(
                    success=False,
                    draft_id=None,
                    status="failed",
                    message="mcp_gmail_tool_error",
                )
            payload = _tool_payload(raw)
            err = _is_error_payload(payload)
            if err:
                return GmailDraftResult(
                    success=False, draft_id=None, status="failed", message=err
                )
            did = _extract_draft_id(payload)
            if did:
                return GmailDraftResult(success=True, draft_id=did, status="draft", message=None)
            return GmailDraftResult(
                success=False,
                draft_id=None,
                status="failed",
                message="mcp_gmail_unexpected_response",
            )
        except Exception as exc:  # noqa: BLE001
            return GmailDraftResult(
                success=False,
                draft_id=None,
                status="failed",
                message=_exception_message(exc),
            )


@dataclass(frozen=True)
class FreeBusyFetchResult:
    """Result of a calendar free/busy query.

    ``intervals`` is ``None`` when the query failed (fail closed). When the query
    succeeded, ``intervals`` is a (possibly empty) merged busy list in IST.
    """

    intervals: list[tuple[datetime, datetime]] | None
    failure_reason: str | None = None


def fetch_busy_intervals_ist(
    settings: Settings, client_source: Any, day: date
) -> FreeBusyFetchResult:
    """Return merged busy intervals in IST, or a failure result if free/busy could not be fetched."""
    cal_id = settings.google_calendar_id
    if not cal_id:
        return FreeBusyFetchResult([], None)
    start = datetime.combine(day, time.min, tzinfo=IST)
    end = start + timedelta(days=1)
    args: dict[str, Any] = {
        "calendar_id": cal_id,
        "time_min": start.isoformat(),
        "time_max": end.isoformat(),
        "time_zone": "Asia/Kolkata",
    }
    tool = settings.mcp_tool_calendar_freebusy
    started = perf_counter()

    async def _go():
        return await _call_tool_with_timeout(settings, client_source, tool, args)

    try:
        raw = _run_async(_go())
        from fastmcp.client.client import CallToolResult as CTR

        if isinstance(raw, CTR) and raw.is_error:
            elapsed_ms = (perf_counter() - started) * 1000
            logger.warning(
                "Calendar freebusy MCP tool returned error: day=%s calendar_id=%s tool=%s elapsed_ms=%.1f",
                day.isoformat(),
                cal_id,
                tool,
                elapsed_ms,
            )
            return FreeBusyFetchResult(None, "mcp_tool_error")
        payload = _tool_payload(raw)
        if payload is not None:
            err = _is_error_payload(payload)
            if err:
                elapsed_ms = (perf_counter() - started) * 1000
                logger.warning(
                    "Calendar freebusy returned service error: day=%s calendar_id=%s tool=%s elapsed_ms=%.1f error=%s",
                    day.isoformat(),
                    cal_id,
                    tool,
                    elapsed_ms,
                    err,
                )
                return FreeBusyFetchResult(None, "calendar_service_error")
        intervals = _extract_busy_intervals(payload)
        merged = _merge_intervals([(s.astimezone(IST), e.astimezone(IST)) for s, e in intervals])
        elapsed_ms = (perf_counter() - started) * 1000
        logger.info(
            "Calendar freebusy succeeded: day=%s calendar_id=%s tool=%s elapsed_ms=%.1f busy_intervals=%d merged_intervals=%d",
            day.isoformat(),
            cal_id,
            tool,
            elapsed_ms,
            len(intervals),
            len(merged),
        )
        return FreeBusyFetchResult(merged, None)
    except TimeoutError:
        elapsed_ms = (perf_counter() - started) * 1000
        logger.warning(
            "Calendar freebusy timed out: day=%s calendar_id=%s tool=%s timeout_s=%.1f elapsed_ms=%.1f",
            day.isoformat(),
            cal_id,
            tool,
            settings.mcp_call_timeout_seconds,
            elapsed_ms,
        )
        return FreeBusyFetchResult(None, "mcp_call_timeout")
    except Exception as exc:
        elapsed_ms = (perf_counter() - started) * 1000
        logger.exception(
            "Calendar freebusy failed unexpectedly: day=%s calendar_id=%s tool=%s elapsed_ms=%.1f error_type=%s error=%s",
            day.isoformat(),
            cal_id,
            tool,
            elapsed_ms,
            type(exc).__name__,
            exc,
        )
        return FreeBusyFetchResult(None, "mcp_unexpected_error")


def matching_slots_via_mcp(
    settings: Settings,
    client_source: Any,
    *,
    preferred_day: date,
    time_window: str | None,
    limit: int,
) -> tuple[list[Slot], bool, str | None]:
    """Returns ``(slots, freebusy_ok, failure_reason)``.

    When free/busy fails, returns ``([], False, reason)`` (no slots; fail closed).
    """
    fb = fetch_busy_intervals_ist(settings, client_source, preferred_day)
    if fb.intervals is None:
        return [], False, fb.failure_reason
    busy = fb.intervals
    start_h = settings.advisor_slot_start_hour
    end_h = settings.advisor_slot_end_hour
    day_start = datetime.combine(preferred_day, time(hour=start_h, minute=0), tzinfo=IST)
    last_start = datetime.combine(preferred_day, time(hour=end_h - 1, minute=30), tzinfo=IST)
    candidates: list[Slot] = []
    cur = day_start
    while cur <= last_start:
        slot_end = cur + timedelta(minutes=30)
        if not _slot_overlaps_busy(cur, slot_end, busy):
            h = cur.hour
            if _window_allows(time_window, h):
                candidates.append(Slot(start=cur, label=_format_ist(cur)))
                if len(candidates) >= limit:
                    break
        cur += timedelta(minutes=30)
    return candidates, True, None


def availability_labels_via_mcp(
    settings: Settings,
    client_source: Any,
    *,
    day: date,
    limit: int = 10,
) -> tuple[list[str], bool, str | None]:
    slots, ok, reason = matching_slots_via_mcp(
        settings,
        client_source,
        preferred_day=day,
        time_window=None,
        limit=limit,
    )
    return [s.label for s in slots], ok, reason
