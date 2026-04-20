# Phase 2 Operator Setup

This document covers everything an operator needs to bring up the Advisor Scheduler with live Google Workspace integration (`USE_MCP=true`).

The full OAuth / FastMCP setup flow is documented separately in [`docs/GOOGLE_MCP_QUICKSTART.md`](GOOGLE_MCP_QUICKSTART.md). The Advisor Scheduler runs a single in-repo Python FastMCP server for Calendar, Sheets, and Gmail (see [`src/advisor_scheduler/integrations/google_workspace/server.py`](../src/advisor_scheduler/integrations/google_workspace/server.py)); there are no external Node MCP processes to manage. This document focuses on the post-auth checklist: resource IDs, sheet preparation, `.env` wiring, tool-name verification, and live testing.

---

## Prerequisites

- Python 3.11+, project installed: `pip install -e ".[dev]"`
- Google Cloud project with Calendar, Sheets, and Gmail APIs enabled
- OAuth desktop credentials at `~/.config/advisor-scheduler/google-oauth-credentials.json`
- Setup helper run once (`python scripts/setup_google_mcp.py`) so the cached refresh token exists at `~/.config/advisor-scheduler/google-token.json`. The same helper writes `mcp-google.json` next to it.

---

## 1 — Gather Resource IDs

### Google Calendar ID

1. Open [Google Calendar](https://calendar.google.com/) and navigate to the advisor's calendar.
2. Click the three-dot menu next to the calendar name → **Settings and sharing**.
3. Scroll to **Integrate calendar** → copy the **Calendar ID** (looks like `abc@group.calendar.google.com` or `user@gmail.com` for the primary calendar).

### Google Sheets Spreadsheet ID

1. Open the pre-booking tracking spreadsheet in Google Sheets (create one if needed).
2. The ID is in the URL: `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`.
3. Copy the `<SPREADSHEET_ID>` segment.

### Sheet tab name

The default tab name is `Advisor Pre-Bookings`. The sheet must contain this tab before the app starts appending rows. Create it if it doesn't exist:

1. Open the spreadsheet.
2. Click the `+` at the bottom to add a sheet.
3. Rename it to exactly `Advisor Pre-Bookings` (case-sensitive).

Add column headers in row 1 (recommended). Order must match what the app writes (columns A–P); the runtime schema is defined in code as `SHEETS_LOG_HEADERS` in [`src/advisor_scheduler/integrations/google_workspace/sheets_schema.py`](../src/advisor_scheduler/integrations/google_workspace/sheets_schema.py):

| A | B | C | D | E | F | G | H | I | J | K | L | M | N | O | P |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| created_at | updated_at | booking_code | topic | intent | requested_day | requested_time_window | confirmed_slot | timezone | status | source | notes | calendar_hold_id | email_draft_id | previous_slot | action_type |

---

## 2 — Configure `.env`

Copy `.env.example` to `.env` and fill in the following (all values are required when `USE_MCP=true`):

```dotenv
# --- Core ---
SECURE_DETAILS_BASE_URL=https://secure.your-domain.com/details
ADVISOR_EMAIL=advisor@your-domain.com

# --- Gemini ---
GEMINI_API_KEY=<your Gemini API key>
GEMINI_MODEL=gemini-2.5-flash

# --- Phase 2: Google Workspace via in-repo Python FastMCP server ---
USE_MCP=true
GOOGLE_CALENDAR_ID=<calendar ID from step 1>
GOOGLE_SHEETS_SPREADSHEET_ID=<spreadsheet ID from step 1>
GOOGLE_SHEETS_TAB=Advisor Pre-Bookings

# Optional. When unset, the adapters auto-launch the in-repo server
# module (src/advisor_scheduler/integrations/google_workspace/server.py)
# via the current Python interpreter.
# MCP_GOOGLE_CONFIG=/Users/<you>/.config/advisor-scheduler/mcp-google.json
```

---

## 3 — Verify Tool Names

The app calls MCP tools by name. The defaults match the in-repo Python FastMCP server. If you point the adapters at a custom server with different labels, override them:

```dotenv
MCP_TOOL_CALENDAR_CREATE_HOLD=calendar_create_hold
MCP_TOOL_CALENDAR_UPDATE_HOLD=calendar_update_hold
MCP_TOOL_CALENDAR_DELETE_HOLD=calendar_delete_hold
MCP_TOOL_CALENDAR_FREEBUSY=calendar_get_freebusy
MCP_TOOL_SHEETS_APPEND_PREBOOKING=sheets_append_prebooking
MCP_TOOL_SHEETS_LIST_PREBOOKINGS=sheets_list_prebookings
MCP_TOOL_GMAIL_CREATE_DRAFT=gmail_create_draft
```

To list the actual tool names from the running FastMCP server:

```bash
python -m advisor_scheduler.cli.mcp_list_tools
```

Cross-check the output against the defaults above and add overrides to `.env` for any mismatches.

---

## 4 — Run the Live MCP Smoke Test

With `.env` fully populated and MCP servers running, execute:

```bash
MCP_LIVE_TEST=1 pytest tests/test_mcp_adapters.py -v -m mcp
```

Expected: all live tests pass (calendar hold creates, sheet row appends, Gmail draft creates, free/busy returns data).

If a test fails:
- Check that the cached refresh token is valid: re-run `python scripts/setup_google_mcp.py`.
- Confirm `GOOGLE_CALENDAR_ID` resolves to a calendar the authenticated account can write to.
- Confirm the `Advisor Pre-Bookings` tab exists in the target spreadsheet.
- Check `MCP_TOOL_*` overrides if you see "tool not found" errors.

---

## 5 — End-to-End Flow Verification

Start the app:

```bash
uvicorn advisor_scheduler.api.app:app --reload
```

Run a manual end-to-end checklist against `http://localhost:8000/chat`:

| Flow | Trigger | Expected downstream effect |
|------|---------|---------------------------|
| New booking | Book → topic → day/time → confirm | Calendar hold created; sheet row appended (`tentative`); Gmail draft created |
| Waitlist | Book → no slots → consent | Sheet row appended (`waitlisted`); Gmail draft created |
| Reschedule | Reschedule → code → new slot → confirm | Latest booking state can be reconstructed from Sheets; a new row is appended (`rescheduled`); calendar hold updated |
| Cancel | Cancel → code → confirm | Latest booking state can be reconstructed from Sheets; a new row is appended (`cancelled`) |
| Availability | Check availability → day | Free/busy queried; windows returned in IST |

Verify each effect in Calendar, Sheets, and Gmail after confirming the conversation.

---

## 6 — Definition of Done

Phase 2 is complete when:

- [ ] `USE_MCP=true`, in-repo Python FastMCP server starts cleanly
- [ ] Live smoke test passes (`MCP_LIVE_TEST=1 pytest tests/test_mcp_adapters.py -m mcp`)
- [ ] All five flows (booking, waitlist, reschedule, cancel, availability) produce correct downstream records
- [ ] `SECURE_DETAILS_BASE_URL` points to a real HTTPS endpoint (placeholder/example URLs are rejected by runtime validation)
- [ ] No booking side effect fires without explicit user confirmation
- [ ] This document is followed top-to-bottom without gaps
