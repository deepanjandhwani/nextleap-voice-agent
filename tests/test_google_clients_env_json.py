from __future__ import annotations

import json

import pytest

from advisor_scheduler.integrations.google_workspace import google_clients


def _token_info() -> dict[str, object]:
    return {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id.apps.googleusercontent.com",
        "client_secret": "client-secret",
        "scopes": list(google_clients.SCOPES),
        "expiry": "2999-01-01T00:00:00Z",
    }


def test_load_credentials_reads_authorized_user_json_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_JSON", json.dumps(_token_info()))
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN", str(tmp_path / "missing-token.json"))
    monkeypatch.setenv("GOOGLE_OAUTH_CREDENTIALS", str(tmp_path / "missing-client.json"))
    monkeypatch.delenv("GOOGLE_OAUTH_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("ADVISOR_MCP_ALLOW_INTERACTIVE_AUTH", raising=False)

    creds = google_clients.load_credentials()

    assert creds.token == "access-token"
    assert creds.refresh_token == "refresh-token"
    assert creds.client_id == "client-id.apps.googleusercontent.com"


def test_credentials_json_env_avoids_missing_file_error(monkeypatch, tmp_path):
    client_config = {
        "installed": {
            "client_id": "client-id.apps.googleusercontent.com",
            "client_secret": "client-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_JSON", raising=False)
    monkeypatch.setenv("GOOGLE_OAUTH_CREDENTIALS_JSON", json.dumps(client_config))
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN", str(tmp_path / "missing-token.json"))
    monkeypatch.setenv("GOOGLE_OAUTH_CREDENTIALS", str(tmp_path / "missing-client.json"))
    monkeypatch.delenv("ADVISOR_MCP_ALLOW_INTERACTIVE_AUTH", raising=False)

    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_TOKEN_JSON"):
        google_clients.load_credentials()
