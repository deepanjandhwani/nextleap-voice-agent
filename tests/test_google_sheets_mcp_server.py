"""Unit tests for in-repo Google MCP server sheet tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from advisor_scheduler.integrations.google_workspace.server import sheets_append_prebooking
from advisor_scheduler.integrations.google_workspace.sheets_schema import (
    SHEETS_LOG_COLUMN_COUNT,
    SHEETS_LOG_HEADERS,
)


def _mock_sheets_api(get_execute_payload: dict) -> MagicMock:
    mock_exec_get = MagicMock()
    mock_exec_get.execute.return_value = get_execute_payload

    mock_exec_update = MagicMock()
    mock_exec_update.execute.return_value = {"updatedRange": "S!A1:P2"}

    mock_values = MagicMock()
    mock_values.get.return_value = mock_exec_get
    mock_values.update.return_value = mock_exec_update

    mock_ss = MagicMock()
    mock_ss.values.return_value = mock_values

    mock_sheets = MagicMock()
    mock_sheets.spreadsheets.return_value = mock_ss
    return mock_sheets


def test_sheets_append_prebooking_prepends_header_when_sheet_empty():
    one_log_row = [[str(i) for i in range(SHEETS_LOG_COLUMN_COUNT)]]
    mock_api = _mock_sheets_api(get_execute_payload={"values": []})
    with patch(
        "advisor_scheduler.integrations.google_workspace.server.google_clients.sheets_service",
        return_value=mock_api,
    ):
        out = sheets_append_prebooking(
            spreadsheet_id="sp1",
            sheet="S",
            values=one_log_row,
        )
    assert "error" not in out
    assert out["updated_rows"] == 2
    v_api = mock_api.spreadsheets.return_value.values.return_value
    update_call = v_api.update
    assert update_call.called
    kwargs = update_call.call_args.kwargs
    assert kwargs["range"] == "S!A1:P2"
    body_vals = kwargs["body"]["values"]
    assert body_vals[0] == list(SHEETS_LOG_HEADERS)
    assert body_vals[1] == one_log_row[0]


def test_sheets_append_prebooking_skips_extra_header_when_column_a_has_data():
    one_log_row = [[str(i) for i in range(SHEETS_LOG_COLUMN_COUNT)]]
    mock_api = _mock_sheets_api(
        get_execute_payload={"values": [["created_at"], ["2026-01-01T00:00:00"]]}
    )
    with patch(
        "advisor_scheduler.integrations.google_workspace.server.google_clients.sheets_service",
        return_value=mock_api,
    ):
        out = sheets_append_prebooking(
            spreadsheet_id="sp1",
            sheet="S",
            values=one_log_row,
        )
    assert "error" not in out
    assert out["updated_rows"] == 1
    kwargs = mock_api.spreadsheets.return_value.values.return_value.update.call_args.kwargs
    assert kwargs["range"] == "S!A3:P3"
    assert kwargs["body"]["values"] == one_log_row
