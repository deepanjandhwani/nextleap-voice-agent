from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache
from time import perf_counter
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Literal

from pydantic import BaseModel, Field

from advisor_scheduler.config import get_settings
from advisor_scheduler.core.engine import ConversationEngine, build_default_engine, process_message
from advisor_scheduler.formatters.voice import build_tts_text, format_for_voice
from advisor_scheduler.integrations.deepgram import (
    DeepgramError,
    audio_mime_type_for_encoding,
    synthesize_speech,
    transcribe_audio,
)

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_engine: ConversationEngine | None = None


def _ensure_engine(*, reason: str) -> ConversationEngine:
    global _engine
    if _engine is not None:
        return _engine
    started = perf_counter()
    logger.info("Initializing conversation engine (%s)", reason)
    try:
        _engine = build_default_engine()
    except Exception:
        elapsed_ms = (perf_counter() - started) * 1000
        logger.exception(
            "Conversation engine initialization failed (%s) after %.1f ms",
            reason,
            elapsed_ms,
        )
        raise
    elapsed_ms = (perf_counter() - started) * 1000
    logger.info("Conversation engine initialized (%s) in %.1f ms", reason, elapsed_ms)
    return _engine


def get_engine() -> ConversationEngine:
    return _ensure_engine(reason="request")


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message: str = ""
    channel: Literal["chat", "voice"] = "chat"


class ChatResponseModel(BaseModel):
    # Each /chat and /voice-turn is stateless; session state lives server-side.
    # Null-valued fields are excluded from JSON output (response_model_exclude_none=True
    # on each route) to reduce payload noise on the typical non-booking turn.
    response: str
    session_state: str
    booking_code: str | None = None
    secure_link: str | None = None
    status: str | None = None
    tts_text: str | None = None


class VoiceTurnResponseModel(ChatResponseModel):
    transcript: str
    audio_base64: str | None = None
    audio_mime_type: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        _ensure_engine(reason="startup")
    except Exception:
        logger.exception("Startup engine warm-up failed")
    yield


app = FastAPI(title="Advisor Scheduler", version="0.1.0", lifespan=lifespan)


def _cors_allow_origins() -> list[str]:
    s = get_settings()
    origins: list[str] = [
        "null",
        f"http://127.0.0.1:{s.api_port}",
        f"http://localhost:{s.api_port}",
    ]
    pub = (s.public_base_url or "").strip().rstrip("/")
    if pub:
        origins.append(pub)
    seen: set[str] = set()
    out: list[str] = []
    for o in origins:
        if o not in seen:
            seen.add(o)
            out.append(o)
    return out


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/secure-details")
def secure_details() -> FileResponse:
    """Landing page for the booking-code query link (SECURE_DETAILS_BASE_URL + ?code=)."""
    return FileResponse(_STATIC_DIR / "secure-details.html")


@app.post("/chat", response_model=ChatResponseModel, response_model_exclude_none=True)
def chat(req: ChatRequest) -> ChatResponseModel:
    request_started = perf_counter()
    engine_started = perf_counter()
    eng = get_engine()
    engine_ms = (perf_counter() - engine_started) * 1000
    try:
        process_started = perf_counter()
        out = process_message(eng, req.session_id, req.message)
        process_ms = (perf_counter() - process_started) * 1000
    except Exception:
        logger.exception("Unhandled error in process_message")
        return ChatResponseModel(**_error_payload())
    payload = _build_response_payload(asdict(out), channel=req.channel)
    total_ms = (perf_counter() - request_started) * 1000
    logger.info(
        "/chat completed session_id=%s state=%s channel=%s total_ms=%.1f engine_ms=%.1f process_ms=%.1f",
        req.session_id,
        payload.get("session_state"),
        req.channel,
        total_ms,
        engine_ms,
        process_ms,
    )
    return ChatResponseModel(**payload)


@lru_cache(maxsize=32)
def _tts_cache_call(text_hash: str, text: str, tts_model: str, tts_encoding: str) -> bytes:
    """LRU-cached wrapper around synthesize_speech keyed by text content."""
    return synthesize_speech(text, settings=get_settings())


def _get_tts_audio(text: str, settings) -> bytes:
    text_hash = hashlib.md5(text.encode()).hexdigest()
    return _tts_cache_call(
        text_hash,
        text,
        settings.deepgram_tts_model or "",
        settings.deepgram_tts_encoding or "",
    )


@app.post("/voice-turn", response_model=VoiceTurnResponseModel, response_model_exclude_none=True)
async def voice_turn(request: Request, session_id: str = Query(..., min_length=1)) -> VoiceTurnResponseModel:
    request_started = perf_counter()
    settings = get_settings()
    if not (settings.deepgram_api_key or "").strip():
        raise HTTPException(status_code=503, detail="Deepgram is not configured.")

    audio_bytes = await request.body()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio payload was empty.")

    stt_started = perf_counter()
    try:
        transcript = transcribe_audio(
            audio_bytes,
            content_type=request.headers.get("content-type", "application/octet-stream"),
            settings=settings,
        )
    except DeepgramError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    stt_ms = (perf_counter() - stt_started) * 1000

    if not transcript:
        raise HTTPException(status_code=400, detail="No speech detected in uploaded audio.")

    engine_started = perf_counter()
    eng = get_engine()
    try:
        out = process_message(eng, session_id, transcript)
        payload = _build_response_payload(asdict(out), channel="voice")
    except Exception:
        logger.exception("Unhandled error in process_message")
        payload = _build_response_payload(_error_payload(), channel="voice")
    engine_ms = (perf_counter() - engine_started) * 1000

    audio_base64: str | None = None
    audio_mime_type = audio_mime_type_for_encoding(settings.deepgram_tts_encoding)
    tts_text = payload.get("tts_text") or payload.get("response") or ""
    tts_ms = 0.0
    if tts_text:
        tts_started = perf_counter()
        try:
            tts_audio = _get_tts_audio(tts_text, settings)
            audio_base64 = base64.b64encode(tts_audio).decode("ascii")
        except DeepgramError:
            logger.exception("Deepgram TTS failed for voice turn")
        tts_ms = (perf_counter() - tts_started) * 1000

    total_ms = (perf_counter() - request_started) * 1000
    logger.info(
        "/voice-turn completed session_id=%s state=%s total_ms=%.1f stt_ms=%.1f engine_ms=%.1f tts_ms=%.1f",
        session_id,
        payload.get("session_state"),
        total_ms,
        stt_ms,
        engine_ms,
        tts_ms,
    )

    return VoiceTurnResponseModel(
        transcript=transcript,
        audio_base64=audio_base64,
        audio_mime_type=audio_mime_type,
        **payload,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _build_response_payload(
    payload: dict[str, str | None],
    *,
    channel: Literal["chat", "voice"],
) -> dict[str, str | None]:
    if channel != "voice":
        return payload

    settings = get_settings()
    formatted = format_for_voice(payload["response"] or "")
    payload["response"] = formatted
    payload["tts_text"] = build_tts_text(
        formatted,
        payload.get("booking_code"),
        settings.voice_secure_followup_spoken,
    )
    return payload


def _error_payload() -> dict[str, str | None]:
    return {
        "response": "I'm sorry, something went wrong on my end. Please try again.",
        "session_state": "identify_intent",
        "booking_code": None,
        "secure_link": None,
        "status": None,
        "tts_text": None,
    }
