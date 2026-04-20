#!/usr/bin/env python3
"""One-time setup helper for the in-repo Python FastMCP Google server.

Run this once after dropping your Google OAuth desktop credentials at
``~/.config/advisor-scheduler/google-oauth-credentials.json``. The
script:

  1. Checks for the credentials file.
  2. Runs the OAuth installed-app flow (opens a browser) to grant
     Calendar, Sheets, and Gmail scopes in one consent and caches the
     token at ``~/.config/advisor-scheduler/google-token.json``.
  3. Writes a single FastMCP client config that launches the in-repo
     server module via the current Python interpreter.

After this completes, the FastMCP child process can refresh tokens
silently — there is no Node MCP server, no per-service ``PORT``, and no
stdio/HTTP mode mismatch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    print("Google FastMCP Setup (in-repo Python server)")
    print("=" * 50)
    print()

    config_dir = Path.home() / ".config" / "advisor-scheduler"
    config_dir.mkdir(parents=True, exist_ok=True)
    creds_path = config_dir / "google-oauth-credentials.json"
    token_path = config_dir / "google-token.json"
    mcp_config_path = config_dir / "mcp-google.json"

    print(f"Configuration directory: {config_dir}")
    print()

    if not creds_path.exists():
        print("⚠️  OAuth credentials file not found!")
        print()
        print("Please follow these steps first:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Enable Calendar API, Sheets API, and Gmail API")
        print("3. Create OAuth 2.0 Desktop credentials")
        print("4. Download the credentials JSON file")
        print(f"5. Save it to: {creds_path}")
        print()
        print("Then run this script again.")
        sys.exit(1)

    print(f"✓ Found OAuth credentials at {creds_path}")
    print()

    print("Step 1: Authorising Google Calendar, Sheets, and Gmail...")
    print("        A browser window will open; complete the consent flow.")
    print()
    try:
        from advisor_scheduler.integrations.google_workspace.google_clients import (
            run_interactive_setup,
        )
    except ImportError as exc:
        print(f"error: missing dependencies: {exc}", file=sys.stderr)
        print('       Install with: pip install -e ".[mcp]"', file=sys.stderr)
        sys.exit(1)

    try:
        run_interactive_setup()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"✓ Token cached at {token_path}")
    print()

    mcp_config = {
        "mcpServers": {
            "advisor-google-workspace": {
                "command": sys.executable,
                "args": ["-m", "advisor_scheduler.integrations.google_workspace.server"],
                "env": {},
            }
        }
    }
    mcp_config_path.write_text(json.dumps(mcp_config, indent=2), encoding="utf-8")
    print(f"✓ Wrote FastMCP client config to {mcp_config_path}")
    print()

    print("Step 2: Update your .env (or rely on the in-repo default):")
    print()
    print("   USE_MCP=true")
    print("   GOOGLE_CALENDAR_ID=your-email@gmail.com")
    print("   GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id")
    print("   GOOGLE_SHEETS_TAB=Advisor Pre-Bookings")
    print(f"   MCP_GOOGLE_CONFIG={mcp_config_path}")
    print()
    print("   (MCP_GOOGLE_CONFIG is optional — if unset, the adapter")
    print("    auto-launches the in-repo server module.)")
    print()
    print("Step 3: Verify the setup:")
    print("   python -m advisor_scheduler.cli.mcp_list_tools")
    print()


if __name__ == "__main__":
    main()
