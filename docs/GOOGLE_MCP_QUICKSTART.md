# Quick Start: Google FastMCP (in-repo Python server)

**See also:** [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md) for where MCP fits in the app.

This guide walks you through authenticating the in-repo Python FastMCP
server that powers Calendar, Sheets, and Gmail for the Advisor
Scheduler. There is **one** Python server in this repo — no Node MCP
servers, no per-service `PORT` config, no separate stdio/HTTP
transports to keep aligned.

## Prerequisites

- Python 3.11+ with this project installed, including the MCP extras:
  `pip install -e ".[mcp]"`
- A Google account with access to Calendar, Sheets, and Gmail
- A Google Cloud project where you can enable APIs

## Step 1: Google Cloud Console Setup (5–10 minutes)

### 1.1 Create Project & Enable APIs

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Go to **APIs & Services** → **Enable APIs and Services**
4. Enable these three APIs:
   - **Google Calendar API**
   - **Google Sheets API**
   - **Gmail API**

### 1.2 Configure OAuth Consent Screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Choose **External** user type
3. Fill in required fields:
   - App name: `Advisor Scheduler`
   - User support email: your email
   - Developer contact: your email
4. Click **Save and Continue**
5. On the **Scopes** page, click **Add or Remove Scopes** and add:
   - `https://www.googleapis.com/auth/calendar`
   - `https://www.googleapis.com/auth/spreadsheets`
   - `https://www.googleapis.com/auth/gmail.compose`
6. Click **Update** → **Save and Continue**
7. Add test users: add your Gmail address
8. Click **Save and Continue**

### 1.3 Create OAuth Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **OAuth client ID**
3. Choose **Desktop app** as application type
4. Name: `Advisor Scheduler MCP`
5. Click **Create**
6. **Download JSON**
7. Save it at:

   ```bash
   mkdir -p ~/.config/advisor-scheduler
   mv ~/Downloads/client_secret_*.json \
     ~/.config/advisor-scheduler/google-oauth-credentials.json
   ```

## Step 2: One-time OAuth + Config Generation

Run the setup helper:

```bash
python scripts/setup_google_mcp.py
```

This script:

- Confirms `google-oauth-credentials.json` is in place.
- Opens a browser to grant Calendar, Sheets, and Gmail scopes in one
  consent flow.
- Caches the refresh token at
  `~/.config/advisor-scheduler/google-token.json` (chmod `600`).
- Writes `~/.config/advisor-scheduler/mcp-google.json`, a FastMCP
  client config that launches the in-repo server module via the
  current Python interpreter.

After this completes the FastMCP child process refreshes tokens
silently — you do not need to re-authorize unless the refresh token is
revoked.

## Step 3: Get Google Resource IDs

### 3.1 Calendar ID

For your primary calendar, this is your Gmail address:

```env
GOOGLE_CALENDAR_ID=your-email@gmail.com
```

For a different calendar, open
[Google Calendar](https://calendar.google.com/) → settings → select
the calendar → **Integrate calendar** → copy **Calendar ID**.

### 3.2 Sheets Spreadsheet ID

Open the target sheet and copy the ID from the URL
(`.../spreadsheets/d/SPREADSHEET_ID/edit`). Make sure a tab named
**Advisor Pre-Bookings** exists (or set `GOOGLE_SHEETS_TAB`). For a tidy
operator view, add the 16 column headers in row 1 in the order documented in
[`docs/phase2_operator_setup.md`](phase2_operator_setup.md) (they match
`SHEETS_LOG_HEADERS` in `sheets_schema.py`).

## Step 4: Update `.env`

Local development:

```env
USE_MCP=true

GOOGLE_CALENDAR_ID=your-email@gmail.com
GOOGLE_SHEETS_SPREADSHEET_ID=1ABC_your_spreadsheet_id_xyz
GOOGLE_SHEETS_TAB=Advisor Pre-Bookings
PUBLIC_BASE_URL=http://127.0.0.1:8000

# Optional. When unset, the adapters auto-launch the in-repo server
# module directly. The setup helper writes this file:
MCP_GOOGLE_CONFIG=/Users/you/.config/advisor-scheduler/mcp-google.json

ADVISOR_EMAIL=your-email@gmail.com
```

Vercel / serverless deployment:

```env
PUBLIC_BASE_URL=https://your-project.vercel.app
USE_MCP=true
GOOGLE_CALENDAR_ID=your-email@gmail.com
GOOGLE_SHEETS_SPREADSHEET_ID=1ABC_your_spreadsheet_id_xyz
GOOGLE_SHEETS_TAB=Advisor Pre-Bookings
GOOGLE_OAUTH_TOKEN_JSON={"token":"...","refresh_token":"...",...}
GOOGLE_OAUTH_CREDENTIALS_JSON={"installed":{...}}
ADVISOR_EMAIL=your-email@gmail.com
```

Use the full contents of `~/.config/advisor-scheduler/google-token.json`
for `GOOGLE_OAUTH_TOKEN_JSON` and
`~/.config/advisor-scheduler/google-oauth-credentials.json` for
`GOOGLE_OAUTH_CREDENTIALS_JSON`. File path variables such as
`GOOGLE_OAUTH_TOKEN` and `GOOGLE_OAUTH_CREDENTIALS` are only useful when
those files exist on the same machine that runs the app.

## Step 5: Verify Setup

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

## Step 6: Run a Live Smoke Test (optional)

```bash
export MCP_LIVE_TEST=1
python -m pytest tests/test_mcp_adapters.py -m mcp -v
```

## Troubleshooting

### "No usable Google token at …" when starting the server

The FastMCP child process refuses to spawn a browser from a stdio
context. Re-run `python scripts/setup_google_mcp.py` to refresh the
cached token. On Vercel, paste that token file into
`GOOGLE_OAUTH_TOKEN_JSON` and redeploy.

### "Google OAuth client credentials not found" on Vercel

Set `GOOGLE_OAUTH_CREDENTIALS_JSON` to the full desktop OAuth client
JSON and redeploy. Vercel cannot read
`~/.config/advisor-scheduler/google-oauth-credentials.json` from your
local machine.

### "Token expired" or 401 errors

Refresh tokens can be revoked from your Google Account security page.
Delete `~/.config/advisor-scheduler/google-token.json` and re-run the
setup helper.

### "Permission denied" on Sheet or Calendar

Verify the IDs in `.env` are correct and that the authenticated Google
account has access. For shared resources, check sharing permissions.

### Tool names don't match

If you point `MCP_GOOGLE_CONFIG` at a different (custom) FastMCP
server, override the tool labels:

```env
MCP_TOOL_CALENDAR_CREATE_HOLD=…
MCP_TOOL_CALENDAR_FREEBUSY=…
MCP_TOOL_SHEETS_APPEND_PREBOOKING=…
MCP_TOOL_GMAIL_CREATE_DRAFT=…
```

## Done!

The app can now:
- Query calendar free/busy for real availability
- Create tentative calendar holds
- Append append-only event-log rows to Google Sheets
- Create Gmail drafts for advisor notifications (drafts only — never
  auto-sent in Phase 1)

Start the app and run a booking flow.
