#!/usr/bin/env python3
"""
End-to-end voice pipeline validator.

Validates the Deepgram TTS/STT pipeline and slash sanitization without needing a browser.

Usage:
    python scripts/test_voice_e2e.py

Requirements:
    DEEPGRAM_API_KEY must be set in environment or .env file.
    The FastAPI server must NOT be running before this script starts (it starts one internally).

Steps:
    1. Config check    — verify DEEPGRAM_API_KEY is set
    2. TTS test        — synthesize known text (including "/"), assert > 1 KB of audio
    3. STT test        — send TTS audio back to Deepgram, assert non-empty transcript
    4. API round-trip  — start FastAPI in-process, POST to /voice-turn, assert full response
    5. Slash sanitize  — run format_for_voice + build_tts_text, assert no raw " / " in TTS text
"""
from __future__ import annotations

import base64
import os
import sys
import textwrap
import threading
import time
import traceback
from pathlib import Path

# Allow running from project root without installing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Load .env before importing settings
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # python-dotenv not installed; rely on real env vars


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

_results: list[tuple[str, bool, str]] = []


def report(name: str, passed: bool, detail: str = "") -> None:
    tag = PASS if passed else FAIL
    line = f"  [{tag}] {name}"
    if detail:
        wrapped = textwrap.indent(detail, "         ")
        line += f"\n{wrapped}"
    print(line)
    _results.append((name, passed, detail))


# ---------------------------------------------------------------------------
# Step 1 — Config check
# ---------------------------------------------------------------------------

def step_config() -> str | None:
    """Return the API key if set, else report failure and return None."""
    print("\n[1/5] Config check")
    api_key = (os.environ.get("DEEPGRAM_API_KEY") or "").strip()
    if not api_key:
        report("DEEPGRAM_API_KEY is set", False, "Set DEEPGRAM_API_KEY in .env or environment.")
        return None
    report("DEEPGRAM_API_KEY is set", True)
    return api_key


# ---------------------------------------------------------------------------
# Step 2 — TTS test
# ---------------------------------------------------------------------------

_TTS_TEXT = "Hello, this is a test. Topics available include KYC / Onboarding and SIP / Mandates."
_MIN_AUDIO_BYTES = 1024


def step_tts(api_key: str) -> bytes | None:
    """Synthesize _TTS_TEXT, assert > 1 KB. Returns audio bytes on success."""
    print("\n[2/5] TTS test — synthesize text with '/'")
    try:
        from advisor_scheduler.config import Settings
        from advisor_scheduler.integrations.deepgram import synthesize_speech

        settings = Settings(deepgram_api_key=api_key)
        audio = synthesize_speech(_TTS_TEXT, settings=settings)
    except Exception as exc:
        report("TTS returns audio bytes", False, traceback.format_exc())
        return None

    ok = len(audio) > _MIN_AUDIO_BYTES
    report(
        f"TTS audio > {_MIN_AUDIO_BYTES} bytes",
        ok,
        f"Got {len(audio)} bytes." if not ok else f"{len(audio)} bytes received.",
    )
    return audio if ok else None


# ---------------------------------------------------------------------------
# Step 3 — STT test
# ---------------------------------------------------------------------------

def step_stt(audio: bytes, api_key: str) -> str | None:
    """Send TTS audio back through STT, assert non-empty transcript."""
    print("\n[3/5] STT test — transcribe TTS audio")
    try:
        from advisor_scheduler.config import Settings
        from advisor_scheduler.integrations.deepgram import transcribe_audio

        settings = Settings(deepgram_api_key=api_key)
        # Deepgram TTS returns mp3 by default
        content_type = "audio/mpeg"
        transcript = transcribe_audio(audio, content_type=content_type, settings=settings)
    except Exception as exc:
        report("STT returns non-empty transcript", False, traceback.format_exc())
        return None

    ok = bool(transcript.strip())
    report(
        "STT returns non-empty transcript",
        ok,
        f"Transcript: {transcript!r}" if transcript else "Empty transcript returned.",
    )
    return transcript if ok else None


# ---------------------------------------------------------------------------
# Step 4 — API round-trip via /voice-turn
# ---------------------------------------------------------------------------

def _start_server(host: str = "127.0.0.1", port: int = 18765) -> threading.Thread:
    """Start the FastAPI app in a background thread. Returns the thread."""
    import uvicorn
    from advisor_scheduler.api.app import app

    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    server = uvicorn.Server(config)

    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    return t


def step_api_round_trip(audio: bytes, api_key: str) -> None:
    """Start FastAPI, POST audio to /voice-turn, assert transcript + response + audio_base64."""
    print("\n[4/5] API round-trip — POST to /voice-turn")

    try:
        import httpx
        import uvicorn  # noqa: F401 — just check it's importable
    except ImportError as exc:
        report("API round-trip", False, f"Missing dependency: {exc}")
        return

    os.environ.setdefault("DEEPGRAM_API_KEY", api_key)

    host, port = "127.0.0.1", 18765
    base = f"http://{host}:{port}"

    _start_server(host, port)

    # Wait for server to be ready
    for _ in range(20):
        try:
            httpx.get(f"{base}/health", timeout=1).raise_for_status()
            break
        except Exception:
            time.sleep(0.3)
    else:
        report("API round-trip", False, "Server did not start within 6 seconds.")
        return

    try:
        resp = httpx.post(
            f"{base}/voice-turn",
            params={"session_id": "e2e-test-session"},
            content=audio,
            headers={"Content-Type": "audio/mpeg"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        report("API round-trip", False, traceback.format_exc())
        return

    transcript_ok = bool(data.get("transcript", "").strip())
    response_ok = bool(data.get("response", "").strip())
    audio_ok = bool(data.get("audio_base64", "").strip())

    report("transcript populated", transcript_ok, repr(data.get("transcript", "")))
    report("response populated", response_ok, repr(data.get("response", ""))[:120])
    report(
        "audio_base64 populated",
        audio_ok,
        f"{len(data.get('audio_base64', ''))} chars of base64" if audio_ok else "Missing audio_base64.",
    )

    # Verify the audio_base64 decodes to substantial bytes
    if audio_ok:
        decoded = base64.b64decode(data["audio_base64"])
        big_enough = len(decoded) > _MIN_AUDIO_BYTES
        report(
            f"Decoded TTS audio > {_MIN_AUDIO_BYTES} bytes",
            big_enough,
            f"{len(decoded)} bytes decoded.",
        )


# ---------------------------------------------------------------------------
# Step 5 — Slash sanitization
# ---------------------------------------------------------------------------

def step_slash_sanitization() -> None:
    """Verify format_for_voice + build_tts_text strips all ' / ' from TTS text."""
    print("\n[5/5] Slash sanitization — no raw '/' in TTS text")

    try:
        from advisor_scheduler.formatters.voice import build_tts_text, format_for_voice
    except Exception as exc:
        report("Slash sanitization imports", False, traceback.format_exc())
        return

    cases = [
        "I can help with KYC / Onboarding.",
        "Topics: SIP / Mandates and Portfolio / Review.",
        "KYC / Onboarding; SIP / Mandates; Portfolio / Review.",
        _TTS_TEXT,
    ]

    all_ok = True
    for raw in cases:
        voiced = format_for_voice(raw)
        tts = build_tts_text(voiced, None, "")
        if " / " in tts:
            report(f"No ' / ' in TTS for {raw!r:.50}", False, f"Got: {tts!r:.80}")
            all_ok = False

    if all_ok:
        report("No ' / ' in any TTS output", True, f"Checked {len(cases)} cases.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("  Deepgram Voice E2E Test")
    print("=" * 60)

    api_key = step_config()
    if not api_key:
        _print_summary()
        return 1

    audio = step_tts(api_key)
    if audio:
        step_stt(audio, api_key)
        step_api_round_trip(audio, api_key)
    else:
        print("\n[3/5] STT test — SKIPPED (no TTS audio)")
        print("\n[4/5] API round-trip — SKIPPED (no TTS audio)")

    step_slash_sanitization()

    return _print_summary()


def _print_summary() -> int:
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    failed = total - passed
    print("\n" + "=" * 60)
    print(f"  Results: {passed}/{total} passed" + (f", {failed} failed" if failed else ""))
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
