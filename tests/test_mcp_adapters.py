from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from advisor_scheduler.config import Settings
from advisor_scheduler.integrations.google_workspace.mcp import (
    CalendarMcpAdapter,
    GmailMcpAdapter,
    SheetsMcpAdapter,
    default_in_repo_mcp_command,
    fetch_busy_intervals_ist,
    list_mcp_tool_names,
    load_mcp_client_source,
    matching_slots_via_mcp,
)
from advisor_scheduler.integrations.google_workspace.sheets_schema import (
    SHEETS_LOG_HEADERS,
    sheets_log_write_range,
    sheets_row_to_cell_strings,
)
from advisor_scheduler.integrations.google_workspace.stubs import (
    CalendarHoldRequest,
    GmailDraftRequest,
    SheetsRow,
)

IST = ZoneInfo("Asia/Kolkata")


def _ctr(*, data=None, is_error=False):
    from fastmcp.client.client import CallToolResult

    return CallToolResult(
        content=[],
        structured_content=None,
        meta=None,
        data=data,
        is_error=is_error,
    )


def test_list_mcp_tool_names_calls_server():
    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._list_mcp_tool_names_async",
        new=AsyncMock(return_value=["calendar_create_hold", "calendar_get_freebusy"]),
    ):
        assert list_mcp_tool_names({}) == ["calendar_create_hold", "calendar_get_freebusy"]


def test_list_mcp_tool_names_works_with_running_event_loop():
    async def _run() -> list[str]:
        with patch(
            "advisor_scheduler.integrations.google_workspace.mcp._list_mcp_tool_names_async",
            new=AsyncMock(return_value=["calendar_create_hold", "calendar_get_freebusy"]),
        ):
            return list_mcp_tool_names({})

    assert asyncio.run(_run()) == ["calendar_create_hold", "calendar_get_freebusy"]


def test_load_mcp_client_source_from_file(tmp_path: Path):
    p = tmp_path / "mcp.json"
    cfg = {"mcpServers": {"gws": {"url": "http://localhost:9/mcp"}}}
    p.write_text(json.dumps(cfg), encoding="utf-8")
    s = Settings(mcp_google_config=str(p))
    assert load_mcp_client_source(s) == cfg


def test_load_mcp_client_source_defaults_to_in_repo_server():
    s = Settings()
    src = load_mcp_client_source(s)
    assert isinstance(src, dict)
    server = src["mcpServers"]["advisor-google-workspace"]
    assert server["args"] == [
        "-m",
        "advisor_scheduler.integrations.google_workspace.server",
    ]


def test_default_in_repo_mcp_command_uses_python():
    src = default_in_repo_mcp_command()
    assert "advisor-google-workspace" in src["mcpServers"]


def test_calendar_mcp_create_hold_success():
    settings = Settings(
        use_mcp=True,
        mcp_google_config="{}",
        google_calendar_id="primary",
    )
    start = datetime(2026, 4, 20, 10, 0, tzinfo=IST)
    end = datetime(2026, 4, 20, 10, 30, tzinfo=IST)
    req = CalendarHoldRequest(
        title="Advisor Q&A — KYC — NL-A001",
        start_time=start,
        end_time=end,
        timezone="Asia/Kolkata",
        status="tentative",
        metadata={"booking_code": "NL-A001"},
    )
    adapter = CalendarMcpAdapter(settings, {"mcpServers": {}})
    call_tool = AsyncMock(return_value=_ctr(data={"id": "evt-xyz"}))
    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=call_tool,
    ):
        r = adapter.create_hold(req)
    assert r.success
    assert r.external_id == "evt-xyz"
    assert call_tool.await_args.args[1] == "calendar_create_hold"
    args = call_tool.await_args.args[2]
    assert args["calendar_id"] == "primary"
    assert args["status"] == "tentative"


def test_sheets_row_to_cell_strings_matches_documented_header_order():
    row = SheetsRow(
        created_at=datetime(2026, 4, 20, 10, 0, tzinfo=IST),
        updated_at=datetime(2026, 4, 20, 10, 5, tzinfo=IST),
        booking_code="NL-A001",
        topic="KYC / Onboarding",
        intent="book_new",
        requested_day="Monday",
        requested_time_window="morning",
        confirmed_slot="slot-label",
        timezone="Asia/Kolkata",
        status="tentative",
        source="advisor_scheduler",
        notes="n1",
        calendar_hold_id="cal-1",
        email_draft_id="draft-1",
        previous_slot="prev",
        action_type="new_booking",
    )
    cells = sheets_row_to_cell_strings(row)
    assert len(cells) == len(SHEETS_LOG_HEADERS)
    assert cells[2] == "NL-A001"
    assert cells[SHEETS_LOG_HEADERS.index("notes")] == "n1"
    assert cells[SHEETS_LOG_HEADERS.index("action_type")] == "new_booking"


def test_sheets_mcp_append_row_single_call():
    settings = Settings(
        use_mcp=True,
        mcp_google_config="{}",
        google_sheets_spreadsheet_id="sh_1",
        google_sheets_tab="Advisor Pre-Bookings",
    )
    row = SheetsRow(
        created_at=datetime(2026, 4, 20, 10, 0, tzinfo=IST),
        updated_at=datetime(2026, 4, 20, 10, 0, tzinfo=IST),
        booking_code="NL-A001",
        topic="KYC / Onboarding",
        intent="book_new",
        requested_day="Monday",
        requested_time_window="morning",
        confirmed_slot="test",
        timezone="Asia/Kolkata",
        status="tentative",
        source="advisor_scheduler",
    )
    adapter = SheetsMcpAdapter(settings, {})
    call_tool = AsyncMock(
        return_value=_ctr(
            data={
                "updated_range": "Advisor Pre-Bookings!A5:P5",
                "updated_rows": 1,
                "spreadsheet_id": "sh_1",
            }
        )
    )
    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=call_tool,
    ):
        r = adapter.append_row(row)
    assert r.success
    assert r.row_identifier == "Advisor Pre-Bookings!A5:P5"
    assert call_tool.await_count == 1
    assert call_tool.await_args.args[1] == "sheets_append_prebooking"
    args = call_tool.await_args.args[2]
    assert args["spreadsheet_id"] == "sh_1"
    assert args["sheet"] == "Advisor Pre-Bookings"
    assert args["values"][0][2] == "NL-A001"


def test_sheets_log_write_range_targets_explicit_columns():
    assert sheets_log_write_range(sheet="Advisor Pre-Bookings", start_row=5, row_count=1) == (
        "Advisor Pre-Bookings!A5:P5"
    )


def test_sheets_mcp_list_rows_reconstructs_schema():
    settings = Settings(
        use_mcp=True,
        mcp_google_config="{}",
        google_sheets_spreadsheet_id="sh_1",
        google_sheets_tab="Advisor Pre-Bookings",
    )
    adapter = SheetsMcpAdapter(settings, {})
    payload = {
        "rows": [
            list(SHEETS_LOG_HEADERS),
            [
                "2026-04-20T10:00:00+05:30",
                "2026-04-20T10:05:00+05:30",
                "NL-A001",
                "KYC / Onboarding",
                "book_new",
                "Monday",
                "morning",
                "Monday, 20 Apr 2026 at 10:00 IST",
                "Asia/Kolkata",
                "tentative",
                "advisor_scheduler",
                "",
                "cal-1",
                "draft-1",
                "",
                "new_booking",
            ]
        ]
    }
    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=AsyncMock(return_value=_ctr(data=payload)),
    ):
        rows = adapter.list_rows()
    assert len(rows) == 1
    assert rows[0].booking_code == "NL-A001"
    assert rows[0].email_draft_id == "draft-1"


def test_gmail_mcp_create_draft_success():
    settings = Settings(use_mcp=True, mcp_google_config="{}")
    req = GmailDraftRequest(
        to="a@example.com",
        subject="Test",
        body="Hello",
        approval_required=True,
        metadata={},
    )
    adapter = GmailMcpAdapter(settings, {})
    call_tool = AsyncMock(return_value=_ctr(data={"id": "draft-1"}))
    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=call_tool,
    ):
        r = adapter.create_draft(req)
    assert r.success
    assert r.draft_id == "draft-1"
    assert call_tool.await_args.args[1] == "gmail_create_draft"
    assert call_tool.await_args.args[2]["to"] == "a@example.com"


def test_gmail_mcp_create_draft_times_out():
    settings = Settings(use_mcp=True, mcp_google_config="{}", mcp_call_timeout_seconds=0.01)
    req = GmailDraftRequest(
        to="a@example.com",
        subject="Test",
        body="Hello",
        approval_required=True,
        metadata={},
    )
    adapter = GmailMcpAdapter(settings, {})

    async def slow_call(*args, **kwargs):
        await asyncio.sleep(0.05)
        return _ctr(data={"id": "draft-1"})

    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=slow_call,
    ):
        r = adapter.create_draft(req)
    assert not r.success
    assert r.draft_id is None
    assert "timeout" in (r.message or "").lower()


def test_calendar_mcp_update_hold_success():
    settings = Settings(
        use_mcp=True,
        mcp_google_config="{}",
        google_calendar_id="primary",
    )
    start = datetime(2026, 4, 20, 11, 0, tzinfo=IST)
    end = datetime(2026, 4, 20, 11, 30, tzinfo=IST)
    req = CalendarHoldRequest(
        title="Advisor Q&A — KYC — NL-A001",
        start_time=start,
        end_time=end,
        timezone="Asia/Kolkata",
        status="rescheduled",
        metadata={"booking_code": "NL-A001"},
    )
    adapter = CalendarMcpAdapter(settings, {})
    call_tool = AsyncMock(return_value=_ctr(data={"id": "evt-existing"}))
    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=call_tool,
    ):
        r = adapter.update_hold("evt-existing", req)
    assert r.success
    assert r.external_id == "evt-existing"
    assert call_tool.await_args.args[1] == "calendar_update_hold"
    assert call_tool.await_args.args[2]["event_id"] == "evt-existing"


def test_calendar_mcp_delete_hold_success():
    settings = Settings(use_mcp=True, mcp_google_config="{}", google_calendar_id="primary")
    adapter = CalendarMcpAdapter(settings, {})
    call_tool = AsyncMock(return_value=_ctr(data={"deleted": True, "event_id": "evt-1"}))
    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=call_tool,
    ):
        r = adapter.delete_hold("evt-1")
    assert r.success
    assert r.external_id == "evt-1"
    assert call_tool.await_args.args[1] == "calendar_delete_hold"


def test_calendar_mcp_propagates_server_error_payload():
    settings = Settings(use_mcp=True, mcp_google_config="{}", google_calendar_id="primary")
    req = CalendarHoldRequest(
        title="x",
        start_time=datetime(2026, 4, 20, 10, 0, tzinfo=IST),
        end_time=datetime(2026, 4, 20, 10, 30, tzinfo=IST),
        timezone="Asia/Kolkata",
        status="tentative",
        metadata={},
    )
    adapter = CalendarMcpAdapter(settings, {})
    payload = {"error": "calendar_insert_failed", "message": "bad request"}
    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=AsyncMock(return_value=_ctr(data=payload)),
    ):
        r = adapter.create_hold(req)
    assert not r.success
    assert r.message == "bad request"


def test_matching_slots_via_mcp_respects_freebusy():
    settings = Settings(
        use_mcp=True,
        mcp_google_config="{}",
        google_calendar_id="primary",
        advisor_slot_start_hour=10,
        advisor_slot_end_hour=12,
    )
    day = date(2026, 4, 20)

    # In-repo server returns the flat shape; the adapter still accepts the
    # raw Google "calendars" shape for resilience against custom servers.
    busy_payload = {
        "busy": [
            {
                "start": "2026-04-20T04:30:00.000Z",
                "end": "2026-04-20T05:30:00.000Z",
            }
        ]
    }

    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=AsyncMock(return_value=_ctr(data=busy_payload)),
    ):
        slots, ok, failure = matching_slots_via_mcp(
            settings,
            {},
            preferred_day=day,
            time_window="morning",
            limit=4,
        )
    # Busy 04:30–05:30Z == 10:00–11:00 IST; morning window 10:00–12:00 → first free slot starts 11:00 IST
    assert ok
    assert failure is None
    assert len(slots) >= 1
    first = slots[0].start.astimezone(IST)
    assert first.hour == 11 and first.minute == 0
    assert first.date() == day


def test_matching_slots_via_mcp_fails_closed_when_freebusy_errors():
    settings = Settings(
        use_mcp=True,
        mcp_google_config="{}",
        google_calendar_id="primary",
        advisor_slot_start_hour=10,
        advisor_slot_end_hour=18,
    )
    day = date(2026, 4, 20)
    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=AsyncMock(return_value=_ctr(is_error=True)),
    ):
        slots, ok, failure = matching_slots_via_mcp(
            settings,
            {},
            preferred_day=day,
            time_window=None,
            limit=4,
        )
    assert slots == []
    assert ok is False
    assert failure == "mcp_tool_error"


def test_matching_slots_via_mcp_fails_closed_when_freebusy_payload_error():
    settings = Settings(
        use_mcp=True,
        mcp_google_config="{}",
        google_calendar_id="primary",
        advisor_slot_start_hour=10,
        advisor_slot_end_hour=18,
    )
    day = date(2026, 4, 20)
    with patch(
        "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
        new=AsyncMock(return_value=_ctr(data={"error": "freebusy_failed", "message": "upstream"})),
    ):
        slots, ok, failure = matching_slots_via_mcp(
            settings,
            {},
            preferred_day=day,
            time_window=None,
            limit=4,
        )
    assert slots == []
    assert ok is False
    assert failure == "calendar_service_error"


def test_fetch_busy_intervals_ist_returns_unexpected_error_and_logs(caplog):
    settings = Settings(use_mcp=True, mcp_google_config="{}", google_calendar_id="primary")
    day = date(2026, 4, 20)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    with caplog.at_level("ERROR"):
        with patch(
            "advisor_scheduler.integrations.google_workspace.mcp._call_tool_async",
            new=boom,
        ):
            result = fetch_busy_intervals_ist(settings, {}, day)

    assert result.intervals is None
    assert result.failure_reason == "mcp_unexpected_error"
    assert "Calendar freebusy failed unexpectedly" in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.mcp
def test_live_mcp_build_adapters_smoke():
    import os

    if os.environ.get("MCP_LIVE_TEST") != "1":
        pytest.skip("Set MCP_LIVE_TEST=1 and configure .env for live Google MCP")

    from advisor_scheduler.config import get_settings
    from advisor_scheduler.integrations.factory import build_adapters

    settings = get_settings()
    if not settings.use_mcp:
        pytest.skip("use_mcp must be true for live test")

    cal, sheets, gmail = build_adapters(settings)
    assert cal is not None and sheets is not None and gmail is not None


@pytest.mark.mcp
def test_live_mcp_list_tool_names():
    import os

    if os.environ.get("MCP_LIVE_TEST") != "1":
        pytest.skip("Set MCP_LIVE_TEST=1 and configure .env for live Google MCP")

    from advisor_scheduler.config import get_settings

    settings = get_settings()
    if not settings.use_mcp:
        pytest.skip("use_mcp must be true for live test")

    src = load_mcp_client_source(settings)
    names = list_mcp_tool_names(src)
    assert isinstance(names, list)
    expected = {
        settings.mcp_tool_calendar_create_hold,
        settings.mcp_tool_calendar_update_hold,
        settings.mcp_tool_calendar_delete_hold,
        settings.mcp_tool_calendar_freebusy,
        settings.mcp_tool_sheets_append_prebooking,
        settings.mcp_tool_sheets_list_prebookings,
        settings.mcp_tool_gmail_create_draft,
    }
    assert expected.issubset(set(names))
