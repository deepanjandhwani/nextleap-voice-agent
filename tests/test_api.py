import importlib
import base64

from fastapi.testclient import TestClient

from advisor_scheduler.api.app import app
from advisor_scheduler.config import Settings
from advisor_scheduler.types.models import ChatResponse

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_secure_details_page():
    r = client.get("/secure-details")
    assert r.status_code == 200
    assert "Secure contact details" in r.text


def test_chat_endpoint(engine):
    app_mod = importlib.import_module("advisor_scheduler.api.app")
    original = app_mod._engine
    app_mod._engine = engine
    try:
        r = client.post("/chat", json={"session_id": "api-1", "message": "I want to book"})
        assert r.status_code == 200
        body = r.json()
        assert "response" in body and "session_state" in body
    finally:
        app_mod._engine = original


def test_ensure_engine_builds_once_and_caches(monkeypatch):
    app_mod = importlib.import_module("advisor_scheduler.api.app")
    original = app_mod._engine
    built = []

    def fake_build_default_engine():
        built.append("built")
        return object()

    app_mod._engine = None
    monkeypatch.setattr(app_mod, "build_default_engine", fake_build_default_engine)
    try:
        first = app_mod._ensure_engine(reason="test")
        second = app_mod._ensure_engine(reason="test")
        assert first is second
        assert len(built) == 1
    finally:
        app_mod._engine = original


def test_chat_voice_channel_formats_response(monkeypatch):
    app_mod = importlib.import_module("advisor_scheduler.api.app")

    def fake_process(engine, session_id, message):
        return ChatResponse(
            response="- Slot A IST\n- Slot B IST\n\nReply with your choice.",
            session_state="offer_slots",
        )

    monkeypatch.setattr(app_mod, "process_message", fake_process)
    r = client.post(
        "/chat",
        json={
            "session_id": "voice-1",
            "message": "book tomorrow",
            "channel": "voice",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["session_state"] == "offer_slots"
    assert "Options:" in body["response"]
    assert "- Slot" not in body["response"]
    assert body.get("tts_text") == body["response"]


def test_chat_voice_channel_tts_text_with_booking_and_link(monkeypatch):
    app_mod = importlib.import_module("advisor_scheduler.api.app")

    def fake_process(engine, session_id, message):
        return ChatResponse(
            response=(
                "All set. Secure link for contact details: https://app.example/secure?code=NL-HIM6. "
                "Your booking code is NL-HIM6."
            ),
            session_state="confirmed",
            booking_code="NL-HIM6",
        )

    monkeypatch.setattr(app_mod, "process_message", fake_process)
    r = client.post(
        "/chat",
        json={
            "session_id": "voice-2",
            "message": "confirm",
            "channel": "voice",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "https://" not in body["tts_text"]
    assert "NL-HIM6" in body["response"]
    assert "NL-HIM6" not in body["tts_text"]
    assert "six" in body["tts_text"].lower()


def test_voice_turn_endpoint_transcribes_formats_and_returns_audio(monkeypatch):
    app_mod = importlib.import_module("advisor_scheduler.api.app")
    settings = Settings(
        deepgram_api_key="test-key",
        secure_details_base_url="https://secure.nextleap.test/details",
    )

    def fake_transcribe(audio_bytes, *, content_type, settings):
        assert audio_bytes == b"voice-bytes"
        assert content_type == "audio/webm"
        return "Book me for tomorrow morning"

    def fake_process(engine, session_id, message):
        assert session_id == "voice-api-1"
        assert message == "Book me for tomorrow morning"
        return ChatResponse(
            response="- 9:00 AM IST\n- 9:30 AM IST\n\nReply with your choice.",
            session_state="offer_slots",
            booking_code="NL-HIM6",
            secure_link="https://secure.nextleap.test/details?code=NL-HIM6",
            status="tentative",
        )

    def fake_synthesize(text, *, settings):
        assert "Options:" in text
        assert "NL-HIM6" not in text
        return b"audio-response"

    monkeypatch.setattr(app_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(app_mod, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(app_mod, "process_message", fake_process)
    monkeypatch.setattr(app_mod, "synthesize_speech", fake_synthesize)

    r = client.post(
        "/voice-turn",
        params={"session_id": "voice-api-1"},
        content=b"voice-bytes",
        headers={"Content-Type": "audio/webm"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["transcript"] == "Book me for tomorrow morning"
    assert body["session_state"] == "offer_slots"
    assert "Options:" in body["response"]
    assert body["audio_mime_type"] == "audio/mpeg"
    assert base64.b64decode(body["audio_base64"]) == b"audio-response"


def test_voice_turn_endpoint_rejects_empty_transcript(monkeypatch):
    app_mod = importlib.import_module("advisor_scheduler.api.app")
    settings = Settings(deepgram_api_key="test-key")

    monkeypatch.setattr(app_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(
        app_mod,
        "transcribe_audio",
        lambda audio_bytes, *, content_type, settings: "",
    )

    r = client.post(
        "/voice-turn",
        params={"session_id": "voice-api-2"},
        content=b"voice-bytes",
        headers={"Content-Type": "audio/webm"},
    )

    assert r.status_code == 400
    assert r.json()["detail"] == "No speech detected in uploaded audio."
