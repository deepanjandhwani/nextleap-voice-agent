"""Google Workspace API clients with shared OAuth handling.

This module is used by the in-repo FastMCP server (:mod:`server`).
It implements a single OAuth installed-app flow that grants Calendar,
Sheets, and Gmail scopes in one consent step and caches refresh tokens
locally. The advisor scheduler then talks to one in-process Python MCP
server instead of three external Node MCP servers, eliminating the
Gmail ``PORT``/stdio mismatch class of failures we hit before.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "advisor-scheduler"
DEFAULT_CREDENTIALS_FILENAME = "google-oauth-credentials.json"
DEFAULT_TOKEN_FILENAME = "google-token.json"
GOOGLE_OAUTH_CREDENTIALS_JSON_ENV = "GOOGLE_OAUTH_CREDENTIALS_JSON"
GOOGLE_OAUTH_TOKEN_JSON_ENV = "GOOGLE_OAUTH_TOKEN_JSON"

# Combined scopes for one consent across Calendar, Sheets, and Gmail.
SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.compose",
)


def _resolve_path(env_name: str, default: Path) -> Path:
    raw = os.environ.get(env_name)
    return Path(raw).expanduser() if raw else default


def credentials_path() -> Path:
    return _resolve_path(
        "GOOGLE_OAUTH_CREDENTIALS",
        DEFAULT_CONFIG_DIR / DEFAULT_CREDENTIALS_FILENAME,
    )


def token_path() -> Path:
    return _resolve_path(
        "GOOGLE_OAUTH_TOKEN",
        DEFAULT_CONFIG_DIR / DEFAULT_TOKEN_FILENAME,
    )


def _json_object_from_env(env_name: str) -> dict | None:
    raw = os.environ.get(env_name)
    if not raw or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_name} must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{env_name} must contain a JSON object")
    return value


def _credentials_from_token_env() -> Credentials | None:
    token_info = _json_object_from_env(GOOGLE_OAUTH_TOKEN_JSON_ENV)
    if token_info is None:
        return None
    try:
        return Credentials.from_authorized_user_info(token_info, list(SCOPES))
    except ValueError as exc:
        raise ValueError(
            f"{GOOGLE_OAUTH_TOKEN_JSON_ENV} is not a valid Google OAuth token JSON"
        ) from exc


def _interactive_auth_allowed() -> bool:
    """Refuse to spawn a browser when running as a non-interactive MCP child."""
    flag = os.environ.get("ADVISOR_MCP_ALLOW_INTERACTIVE_AUTH", "")
    return flag in {"1", "true", "True"}


def load_credentials() -> Credentials:
    """Load cached Google credentials, refreshing or re-running OAuth as needed.

    Token caching keeps the FastMCP server stdio-friendly: after a one-time
    interactive setup via ``scripts/setup_google_mcp.py``, the server only
    needs to refresh tokens silently.
    """
    tok = token_path()
    creds = _credentials_from_token_env()
    loaded_from_env = creds is not None
    if creds is None and tok.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(tok), list(SCOPES))
        except (ValueError, json.JSONDecodeError):
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        if not loaded_from_env:
            _save_credentials(creds)
        return creds

    creds_path = credentials_path()
    client_config = _json_object_from_env(GOOGLE_OAUTH_CREDENTIALS_JSON_ENV)
    if client_config is None and not creds_path.exists():
        raise FileNotFoundError(
            "Google OAuth client credentials not found. Place desktop OAuth "
            f"client JSON at {creds_path}, set GOOGLE_OAUTH_CREDENTIALS, "
            f"or set {GOOGLE_OAUTH_CREDENTIALS_JSON_ENV}."
        )

    if not _interactive_auth_allowed():
        raise RuntimeError(
            "No usable Google token at "
            f"{tok}. Run `python scripts/setup_google_mcp.py` once to "
            "authorize Calendar, Sheets, and Gmail, or set "
            f"{GOOGLE_OAUTH_TOKEN_JSON_ENV}; the FastMCP server refuses to "
            "open a browser from a stdio child process."
        )

    if client_config is not None:
        flow = InstalledAppFlow.from_client_config(client_config, list(SCOPES))
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), list(SCOPES))
    creds = flow.run_local_server(port=0)
    _save_credentials(creds)
    return creds


def _save_credentials(creds: Credentials) -> None:
    tok = token_path()
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(tok, 0o600)
    except OSError:
        pass


def calendar_service():
    return build("calendar", "v3", credentials=load_credentials(), cache_discovery=False)


def sheets_service():
    return build("sheets", "v4", credentials=load_credentials(), cache_discovery=False)


def gmail_service():
    return build("gmail", "v1", credentials=load_credentials(), cache_discovery=False)


def run_interactive_setup() -> None:
    """Force the OAuth flow to run interactively from a CLI entry point."""
    os.environ["ADVISOR_MCP_ALLOW_INTERACTIVE_AUTH"] = "1"
    creds = load_credentials()
    print(f"Saved Google token to {token_path()}", file=sys.stderr)
    if not creds.valid:
        raise RuntimeError("OAuth flow completed but credentials are not valid")
