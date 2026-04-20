# Summary: Google MCP Integration (in-repo Python FastMCP)

## What Changed (current architecture)

The Advisor Scheduler now talks to Google Calendar, Sheets, and Gmail
through a **single in-repo Python FastMCP server**. The previous
three-server setup that depended on external Node MCP servers
(`@cocal/google-calendar-mcp`, `mcp-google-sheets`,
`@shinzolabs/gmail-mcp`) is gone, along with the runtime-generated
Gmail `PORT` config that pushed Gmail into HTTP mode and broke the
stdio MCP client.

Diagram (current):

```
ChatApp → AdapterLayer → PythonFastMcpServer → {Calendar, Sheets, Gmail} APIs
```

## Files Modified

1. **`src/advisor_scheduler/config.py`**
   - Replaced three MCP config envs with a single
     `MCP_GOOGLE_CONFIG`.
   - Added the new tool name defaults
     (`calendar_create_hold`, `calendar_update_hold`,
     `calendar_delete_hold`, `calendar_get_freebusy`,
     `sheets_append_prebooking`, `sheets_list_prebookings`,
     `gmail_create_draft`).

2. **`src/advisor_scheduler/integrations/google_workspace/mcp.py`**
   - Single `load_mcp_client_source()` that defaults to launching
     the in-repo server module when `MCP_GOOGLE_CONFIG` is unset.
   - Adapters now use the new tool contract; sheets append is one
     atomic call, no read-then-write dance.
   - Free/busy parser still accepts both the in-repo flat
     `{"busy": [...]}` shape and the raw Google
     `{"calendars": {...}}` shape for resilience.

3. **`src/advisor_scheduler/integrations/factory.py`**
   - Builds all three adapters from one shared client source.

4. **`src/advisor_scheduler/services/slot_service.py`**
   - Uses the single MCP source for Calendar free/busy.

5. **`src/advisor_scheduler/cli/mcp_list_tools.py`**
   - Lists tools from the one Google Workspace server and verifies
     against the new expected tool names.

6. **`scripts/setup_google_mcp.py`**
   - One-time helper that runs the OAuth installed-app flow,
     caches the token, and writes the FastMCP client config.

7. **`.env.example`** / **`.env`**
   - Single `MCP_GOOGLE_CONFIG` env, optional. When unset, the
     adapter launches the in-repo Python server module directly.

## Files Created

1. **`src/advisor_scheduler/integrations/google_workspace/server.py`**
   - The in-repo FastMCP server. Exposes the scheduling tools:
     `calendar_create_hold`, `calendar_update_hold`,
     `calendar_delete_hold`, `calendar_get_freebusy`,
     `sheets_append_prebooking`, `sheets_list_prebookings`,
     `gmail_create_draft`.

2. **`src/advisor_scheduler/integrations/google_workspace/google_clients.py`**
   - OAuth installed-app flow + cached refresh tokens, plus
     service builders for Calendar, Sheets, and Gmail. Refuses
     to spawn a browser from a stdio child process.

## Quick Start

1. Install deps:
   ```bash
   pip install -e ".[mcp]"
   ```

2. Drop your OAuth desktop client JSON at
   `~/.config/advisor-scheduler/google-oauth-credentials.json`.

3. Run the one-time setup:
   ```bash
   python scripts/setup_google_mcp.py
   ```
   This opens a browser for one combined consent across Calendar,
   Sheets, and Gmail, then caches the refresh token.

4. Set Google resource IDs in `.env`:
   ```env
   USE_MCP=true
   GOOGLE_CALENDAR_ID=your-email@gmail.com
   GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
   GOOGLE_SHEETS_TAB=Advisor Pre-Bookings
   SECURE_DETAILS_BASE_URL=https://secure.your-domain.com/details
   # Optional; defaults to the in-repo Python server.
   # MCP_GOOGLE_CONFIG=/Users/you/.config/advisor-scheduler/mcp-google.json
   ```

5. Verify:
   ```bash
   python -m advisor_scheduler.cli.mcp_list_tools
   ```

   Expected output:
   ```
   MCP tools (Google Workspace server):

     calendar_create_hold
     calendar_delete_hold
     calendar_get_freebusy
     calendar_update_hold
     gmail_create_draft
     sheets_append_prebooking
     sheets_list_prebookings

   Expected tool names (from settings):
     [ok] calendar create hold: calendar_create_hold
     [ok] calendar update hold: calendar_update_hold
     [ok] calendar delete hold: calendar_delete_hold
     [ok] calendar free/busy: calendar_get_freebusy
     [ok] sheets append prebooking: sheets_append_prebooking
     [ok] sheets list prebookings: sheets_list_prebookings
     [ok] gmail draft: gmail_create_draft
   ```

## Why One Python Server

- Removes the runtime-generated Gmail `PORT` env that pushed
  `@shinzolabs/gmail-mcp` into HTTP mode and broke the stdio client.
- Pins a stable, narrow tool contract on top of
  `google-api-python-client`. The orchestrator never needs to learn
  the raw API shapes.
- Keeps Google logic in the same language and repo as the
  orchestrator, so adapter and server change together.
- Append-only Google **Sheets** (not Docs) remains the pre-booking
  event log; latest row per `booking_code` is the current state.
- Gmail stays draft-only in Phase 1; the server has no `send` tool.

## Test Plan

- All adapter unit tests use mocked FastMCP clients.
- Live smoke tests are gated behind `MCP_LIVE_TEST=1` and `pytest -m mcp`.
