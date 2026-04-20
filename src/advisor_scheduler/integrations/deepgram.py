from __future__ import annotations

from typing import Any

import httpx

from advisor_scheduler.config import Settings

_API_BASE = "https://api.deepgram.com/v1"
_MIN_AUDIO_BYTES = 1000


class DeepgramError(RuntimeError):
    """Raised when a Deepgram API call fails or returns unusable data."""


def audio_mime_type_for_encoding(encoding: str) -> str:
    normalized = encoding.strip().lower()
    if normalized == "mp3":
        return "audio/mpeg"
    if normalized in {"wav", "linear16"}:
        return "audio/wav"
    if normalized == "ogg":
        return "audio/ogg"
    if normalized == "opus":
        return "audio/opus"
    return "application/octet-stream"


def extract_transcript(payload: dict[str, Any]) -> str:
    channels = payload.get("results", {}).get("channels", [])
    if not channels:
        return ""
    alternatives = channels[0].get("alternatives", [])
    if not alternatives:
        return ""
    transcript = alternatives[0].get("transcript", "")
    return transcript.strip() if isinstance(transcript, str) else ""


def transcribe_audio(audio_bytes: bytes, *, content_type: str, settings: Settings) -> str:
    if not audio_bytes:
        raise DeepgramError("Audio payload was empty.")
    if len(audio_bytes) < _MIN_AUDIO_BYTES:
        raise DeepgramError(
            f"Audio payload is too small ({len(audio_bytes)} bytes). "
            "Hold the mic button for at least half a second and try again."
        )

    response = _post(
        "/listen",
        settings=settings,
        headers={"Content-Type": content_type or "application/octet-stream"},
        params={
            "model": settings.deepgram_stt_model,
            "language": settings.deepgram_language,
            "smart_format": "true",
        },
        content=audio_bytes,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise DeepgramError("Deepgram STT returned invalid JSON.") from exc
    return extract_transcript(payload)


def _speak_query_params(settings: Settings) -> dict[str, str]:
    """
    Deepgram only accepts certain encoding/sample_rate combinations.
    MP3 and Opus use fixed output rates; passing a custom sample_rate can 422.
    """
    encoding = settings.deepgram_tts_encoding.strip().lower()
    params: dict[str, str] = {
        "model": settings.deepgram_tts_model,
        "encoding": encoding,
    }
    if encoding in {"mp3", "opus"}:
        return params
    params["sample_rate"] = str(settings.deepgram_tts_sample_rate)
    return params


def synthesize_speech(text: str, *, settings: Settings) -> bytes:
    normalized = text.strip()
    if not normalized:
        return b""

    response = _post(
        "/speak",
        settings=settings,
        headers={"Accept": audio_mime_type_for_encoding(settings.deepgram_tts_encoding)},
        params=_speak_query_params(settings),
        json={"text": normalized},
    )
    if not response.content:
        raise DeepgramError("Deepgram TTS returned empty audio.")
    return response.content


def _post(
    path: str,
    *,
    settings: Settings,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    content: bytes | None = None,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    api_key = (settings.deepgram_api_key or "").strip()
    if not api_key:
        raise DeepgramError("Deepgram is not configured.")

    request_headers = {"Authorization": f"Token {api_key}"}
    if headers:
        request_headers.update(headers)

    try:
        response = httpx.post(
            f"{_API_BASE}{path}",
            headers=request_headers,
            params=params,
            content=content,
            json=json,
            timeout=settings.deepgram_request_timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() or exc.response.reason_phrase
        raise DeepgramError(f"Deepgram request failed: {detail}") from exc
    except httpx.HTTPError as exc:
        raise DeepgramError(f"Deepgram request failed: {exc}") from exc

    return response
