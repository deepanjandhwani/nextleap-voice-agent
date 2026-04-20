import pytest
import httpx

from advisor_scheduler.config import Settings
from advisor_scheduler.integrations.deepgram import (
    DeepgramError,
    audio_mime_type_for_encoding,
    extract_transcript,
    synthesize_speech,
    transcribe_audio,
)


class _FakeResponse:
    def __init__(self, *, payload=None, content=b""):
        self._payload = payload
        self.content = content
        self.text = ""
        self.reason_phrase = "OK"

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON payload")
        return self._payload

    def raise_for_status(self):
        return None


def test_extract_transcript_handles_missing_paths():
    assert extract_transcript({}) == ""


def test_transcribe_audio_parses_deepgram_response(monkeypatch):
    settings = Settings(deepgram_api_key="test-key")
    audio_payload = b"voice-bytes" + b"\x00" * 1000

    def fake_post(url, *, headers, params, content, json, timeout):
        assert url.endswith("/listen")
        assert headers["Authorization"] == "Token test-key"
        assert headers["Content-Type"] == "audio/webm"
        assert params["model"] == settings.deepgram_stt_model
        assert params["language"] == settings.deepgram_language
        assert params["smart_format"] == "true"
        assert content == audio_payload
        assert json is None
        assert timeout == settings.deepgram_request_timeout_seconds
        return _FakeResponse(
            payload={
                "results": {
                    "channels": [
                        {"alternatives": [{"transcript": "Book tomorrow at 9 AM"}]}
                    ]
                }
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    transcript = transcribe_audio(
        audio_payload,
        content_type="audio/webm",
        settings=settings,
    )

    assert transcript == "Book tomorrow at 9 AM"


def test_synthesize_speech_returns_audio_bytes(monkeypatch):
    settings = Settings(deepgram_api_key="test-key", deepgram_tts_encoding="mp3")

    def fake_post(url, *, headers, params, content, json, timeout):
        assert url.endswith("/speak")
        assert headers["Authorization"] == "Token test-key"
        assert headers["Accept"] == "audio/mpeg"
        assert params["model"] == settings.deepgram_tts_model
        assert params["encoding"] == "mp3"
        assert "sample_rate" not in params
        assert content is None
        assert json == {"text": "Hello from Deepgram"}
        assert timeout == settings.deepgram_request_timeout_seconds
        return _FakeResponse(content=b"mp3-bytes")

    monkeypatch.setattr(httpx, "post", fake_post)

    audio_bytes = synthesize_speech("Hello from Deepgram", settings=settings)

    assert audio_bytes == b"mp3-bytes"
    assert audio_mime_type_for_encoding("mp3") == "audio/mpeg"


def test_synthesize_speech_linear16_sends_sample_rate(monkeypatch):
    settings = Settings(
        deepgram_api_key="test-key",
        deepgram_tts_encoding="linear16",
        deepgram_tts_sample_rate=16000,
    )

    def fake_post(url, *, headers, params, content, json, timeout):
        assert params["encoding"] == "linear16"
        assert params["sample_rate"] == "16000"
        return _FakeResponse(content=b"pcm-bytes")

    monkeypatch.setattr(httpx, "post", fake_post)

    assert synthesize_speech("Hi", settings=settings) == b"pcm-bytes"


def test_transcribe_audio_rejects_small_payload():
    settings = Settings(deepgram_api_key="test-key")
    with pytest.raises(DeepgramError, match="too small"):
        transcribe_audio(b"tiny", content_type="audio/webm", settings=settings)


def test_transcribe_audio_rejects_999_bytes():
    settings = Settings(deepgram_api_key="test-key")
    with pytest.raises(DeepgramError, match="too small"):
        transcribe_audio(b"x" * 999, content_type="audio/webm", settings=settings)


def test_transcribe_audio_accepts_min_valid_size(monkeypatch):
    settings = Settings(deepgram_api_key="test-key")

    def fake_post(url, **kwargs):
        return _FakeResponse(
            payload={
                "results": {
                    "channels": [{"alternatives": [{"transcript": "hello"}]}]
                }
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = transcribe_audio(b"x" * 1000, content_type="audio/webm", settings=settings)
    assert result == "hello"
