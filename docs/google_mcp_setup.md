# Google FastMCP Setup Guide

**Also read:** [GOOGLE_MCP_QUICKSTART.md](GOOGLE_MCP_QUICKSTART.md) and [phase2_operator_setup.md](phase2_operator_setup.md) — same in-repo server; this file is the full walkthrough.

The Advisor Scheduler talks to Google Calendar, Sheets, and Gmail
through a single in-repo Python FastMCP server (see
[`src/advisor_scheduler/integrations/google_workspace/server.py`](../src/advisor_scheduler/integrations/google_workspace/server.py)).

This replaces the previous trio of external Node MCP servers. Running
the full surface from one Python process keeps every tool on stdio,
removes the `PORT` indirection that broke Gmail, and pins a stable,
narrow tool contract on top of `google-api-python-client`.

## Prerequisites

- Python 3.11+ with the project installed:
  `pip install -e ".[mcp]"`
- A Google Cloud project where you can enable APIs

## Step 1: Enable APIs

In Google Cloud Console, enable:
- **Google Calendar API**
- **Google Sheets API**
- **Gmail API**

## Step 2: Create OAuth 2.0 Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **OAuth client ID**
3. If prompted, configure the **OAuth consent screen** first:
   - Choose **External**
   - App name: `Advisor Scheduler`
   - Add scopes:
     - `https://www.googleapis.com/auth/calendar`
     - `https://www.googleapis.com/auth/spreadsheets`
     - `https://www.googleapis.com/auth/gmail.compose`
   - Add your Google account as a test user
4. Application type: **Desktop app**
5. Download the JSON file and save it to:
   ```
   ~/.config/advisor-scheduler/google-oauth-credentials.json
   ```

The file looks like:

```json
{
  "installed": {
    "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
    "client_secret": "YOUR_CLIENT_SECRET",
    "redirect_uris": ["http://localhost"]
  }
}
```

## Step 3: Run the Setup Helper

```bash
python scripts/setup_google_mcp.py
```

This:

1. Verifies the OAuth credentials file is present.
2. Runs an installed-app OAuth flow that grants Calendar, Sheets, and
   Gmail scopes in **one** consent.
3. Caches the resulting refresh token at
   `~/.config/advisor-scheduler/google-token.json` (chmod `600`).
4. Writes a FastMCP client config at
   `~/.config/advisor-scheduler/mcp-google.json` that launches the
   in-repo server module via the current Python interpreter.

After this completes, the server can refresh tokens silently — no
further interactive auth is needed unless the refresh token is revoked.

## Step 4: Update `.env`

For local development, file paths are fine:

```env
USE_MCP=true

GOOGLE_CALENDAR_ID=your-email@gmail.com
GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
GOOGLE_SHEETS_TAB=Advisor Pre-Bookings

# Optional. If unset, the adapters auto-launch the in-repo server
# module via the current Python interpreter. The setup helper writes
# the same JSON config and prints its absolute path.
MCP_GOOGLE_CONFIG=/Users/you/.config/advisor-scheduler/mcp-google.json
```

For Vercel or another serverless host, do **not** point at local
`~/.config` files. Paste the JSON contents into environment variables
instead:

```env
PUBLIC_BASE_URL=https://your-project.vercel.app
USE_MCP=true
GOOGLE_CALENDAR_ID=your-email@gmail.com
GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
GOOGLE_SHEETS_TAB=Advisor Pre-Bookings
GOOGLE_OAUTH_TOKEN_JSON={"token":"...","refresh_token":"...",...}
GOOGLE_OAUTH_CREDENTIALS_JSON={"installed":{...}}
```

`GOOGLE_OAUTH_TOKEN_JSON` should be the full contents of
`~/.config/advisor-scheduler/google-token.json`.
`GOOGLE_OAUTH_CREDENTIALS_JSON` should be the full contents of
`~/.config/advisor-scheduler/google-oauth-credentials.json`. The
`redirect_uris: ["http://localhost"]` value in desktop OAuth credentials
is expected because interactive consent happens locally; production only
uses the cached refresh token.

## Step 5: Verify

```bash
python -m advisor_scheduler.cli.mcp_list_tools
```

Expected tool names (from the in-repo server):

- `calendar_create_hold`
- `calendar_update_hold`
- `calendar_delete_hold`
- `calendar_get_freebusy`
- `sheets_append_prebooking`
- `sheets_list_prebookings`
- `gmail_create_draft`

## What's in the In-Repo Server

[`server.py`](../src/advisor_scheduler/integrations/google_workspace/server.py)
exposes exactly these scheduler-focused tools — nothing else. It deliberately does
**not** mirror every Google API capability. Only the surface the
orchestrator needs is exposed, which keeps the failure modes tightly
scoped.

OAuth handling lives in
[`google_clients.py`](../src/advisor_scheduler/integrations/google_workspace/google_clients.py)
and refuses to open a browser from a stdio child process — so the
FastMCP server never hangs waiting for interactive input. The only
place where interactive consent runs is the setup script.

## Troubleshooting

### "No usable Google token at …" when starting the server
Re-run `python scripts/setup_google_mcp.py` to refresh the cached
token. On Vercel, make sure the resulting `google-token.json` contents
are set as `GOOGLE_OAUTH_TOKEN_JSON`.

### "Google OAuth client credentials not found" on Vercel

The deployment is still looking for a local credentials file. Set
`GOOGLE_OAUTH_CREDENTIALS_JSON` to the full contents of
`google-oauth-credentials.json`, redeploy, and confirm the deployed code
includes the JSON-env support in `google_clients.py`.

### Permission errors on Sheet/Calendar
Verify the IDs in `.env` are correct and that the authenticated
Google account has access to the resources.

### Token revoked
Delete `~/.config/advisor-scheduler/google-token.json` and re-run the
setup helper.
