from __future__ import annotations

from functools import lru_cache
from urllib.parse import urljoin, urlparse

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Served by this app at GET /secure-details; use with PUBLIC_BASE_URL in each environment.
DEFAULT_SECURE_DETAILS_PATH = "/secure-details"


def _is_valid_http_origin(url: str) -> bool:
    """True for http(s) URLs with a host that is not a reserved example/placeholder host."""
    parts = urlparse(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return False
    host = parts.netloc.lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if host.startswith("["):
        return True
    host = host.split(":", 1)[0]
    if host in {"example.com", "www.example.com", "your-domain.com", "www.your-domain.com"}:
        return False
    return True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ``python -m advisor_scheduler`` bind address. ``PORT`` is common on PaaS.
    api_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("ADVISOR_API_HOST"),
    )
    api_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("ADVISOR_API_PORT", "PORT"),
    )

    # Full URL (e.g. https://app.example.com/secure-details), OR a path only (e.g. /secure-details)
    # with public_base_url set. Empty means "use public_base_url + DEFAULT_SECURE_DETAILS_PATH" when
    # public_base_url is set.
    secure_details_base_url: str = ""
    # Public origin for this deployment (no trailing path required). Used with path-style
    # secure_details_base_url or when secure_details_base_url is empty.
    public_base_url: str | None = None
    advisor_email: str = "advisor-team@example.com"
    session_timeout_minutes: int = 20
    # Spoken line replacing the secure-link sentence for voice/TTS (contact-details flow).
    voice_secure_followup_spoken: str = (
        "We will email you a secure link to submit your contact details. "
        "Please complete that step when you receive it."
    )
    # Gemini is the default Phase 1 runtime model; settings stay configurable.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_temperature: float = 0.2

    # Server-owned Deepgram voice path (browser records audio; backend does STT/TTS).
    deepgram_api_key: str | None = None
    deepgram_stt_model: str = "nova-3"
    deepgram_tts_model: str = "aura-2-asteria-en"
    deepgram_language: str = "en-IN"
    deepgram_tts_encoding: str = "mp3"
    # Used for encodings where sample_rate is configurable (e.g. linear16). Ignored for mp3/opus/aac.
    deepgram_tts_sample_rate: int = 24000
    deepgram_request_timeout_seconds: float = 30.0

    # Phase 2: Google Workspace via the in-repo Python FastMCP server.
    # When ``use_mcp=False`` adapters are in-process stubs.
    use_mcp: bool = False
    google_calendar_id: str | None = None
    google_sheets_spreadsheet_id: str | None = None
    google_sheets_tab: str = "Advisor Pre-Bookings"

    # FastMCP Client transport. Path to JSON config, inline JSON, or a command
    # string for stdio. Defaults to launching the in-repo server module.
    mcp_google_config: str | None = None
    mcp_call_timeout_seconds: float = 15.0

    # Tool names exposed by the in-repo Python FastMCP server. Override only
    # if pointing the adapters at a custom server with different labels.
    mcp_tool_calendar_create_hold: str = "calendar_create_hold"
    mcp_tool_calendar_update_hold: str = "calendar_update_hold"
    mcp_tool_calendar_delete_hold: str = "calendar_delete_hold"
    mcp_tool_calendar_freebusy: str = "calendar_get_freebusy"
    mcp_tool_sheets_append_prebooking: str = "sheets_append_prebooking"
    mcp_tool_sheets_list_prebookings: str = "sheets_list_prebookings"
    mcp_tool_gmail_create_draft: str = "gmail_create_draft"

    # Working hours (IST) used when deriving 30-minute slots from free/busy.
    advisor_slot_start_hour: int = 9
    advisor_slot_end_hour: int = 18

    def resolved_secure_details_base_url(self) -> str | None:
        """Base URL for contact-details links (query param ``code`` is appended by the engine)."""
        raw = self.secure_details_base_url.strip()
        pub = (self.public_base_url or "").strip()

        if raw.startswith("http://") or raw.startswith("https://"):
            return raw if _is_valid_http_origin(raw) else None

        if raw.startswith("/"):
            if not pub:
                return None
            if not _is_valid_http_origin(pub):
                return None
            return urljoin(pub if pub.endswith("/") else pub + "/", raw)

        if raw:
            return None

        if pub and _is_valid_http_origin(pub):
            return urljoin(pub if pub.endswith("/") else pub + "/", DEFAULT_SECURE_DETAILS_PATH.lstrip("/"))

        return None

    def secure_details_url_is_valid(self) -> bool:
        return self.resolved_secure_details_base_url() is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
