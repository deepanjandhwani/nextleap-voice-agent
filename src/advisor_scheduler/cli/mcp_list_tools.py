from __future__ import annotations

import argparse
import sys

from advisor_scheduler.config import get_settings
from advisor_scheduler.integrations.google_workspace.mcp import (
    list_mcp_tool_names,
    load_mcp_client_source,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "List tools on the configured Google FastMCP server and verify expected "
            "tool names. Defaults to the in-repo Python server when "
            "MCP_GOOGLE_CONFIG is not set."
        ),
    )
    parser.add_argument(
        "--no-check",
        action="store_true",
        help="Print tool names only; do not compare to configured MCP_TOOL_* names.",
    )
    args = parser.parse_args()

    settings = get_settings()

    try:
        src = load_mcp_client_source(settings)
    except (OSError, ValueError) as exc:
        print(f"error: could not load MCP config: {exc}", file=sys.stderr)
        sys.exit(2)

    print("MCP tools (Google Workspace server):\n")
    try:
        names = sorted(list_mcp_tool_names(src))
    except Exception as exc:  # noqa: BLE001
        print(f"  error: {exc}", file=sys.stderr)
        sys.exit(2)

    for name in names:
        print(f"  {name}")
    all_names = set(names)

    if args.no_check:
        return

    expected = [
        ("calendar create hold", settings.mcp_tool_calendar_create_hold),
        ("calendar update hold", settings.mcp_tool_calendar_update_hold),
        ("calendar delete hold", settings.mcp_tool_calendar_delete_hold),
        ("calendar free/busy", settings.mcp_tool_calendar_freebusy),
        ("sheets append prebooking", settings.mcp_tool_sheets_append_prebooking),
        ("sheets list prebookings", settings.mcp_tool_sheets_list_prebookings),
        ("gmail draft", settings.mcp_tool_gmail_create_draft),
    ]
    print()
    print("Expected tool names (from settings):")
    ok = True
    for label, tool_name in expected:
        present = tool_name in all_names
        status = "ok" if present else "missing"
        if not present:
            ok = False
        print(f"  [{status}] {label}: {tool_name}")

    if not ok:
        print(file=sys.stderr)
        print(
            "Set MCP_TOOL_CALENDAR_CREATE_HOLD, MCP_TOOL_CALENDAR_UPDATE_HOLD, "
            "MCP_TOOL_CALENDAR_DELETE_HOLD, MCP_TOOL_CALENDAR_FREEBUSY, "
            "MCP_TOOL_SHEETS_APPEND_PREBOOKING, MCP_TOOL_SHEETS_LIST_PREBOOKINGS, "
            "and MCP_TOOL_GMAIL_CREATE_DRAFT to match the server.",
            file=sys.stderr,
        )
        sys.exit(3)


if __name__ == "__main__":
    main()
