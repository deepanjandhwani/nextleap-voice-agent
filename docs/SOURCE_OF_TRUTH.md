# Documentation and code alignment

**The Python package [`src/advisor_scheduler/`](../src/advisor_scheduler/) is the source of truth.** Files under `docs/` describe that behavior. If something in `docs/` disagrees with the code, **trust the code** and update the doc.

## Quick map: where behavior lives

| Concern | Authoritative module(s) |
|--------|-------------------------|
| HTTP API routes | [`api/app.py`](../src/advisor_scheduler/api/app.py) |
| Voice-friendly reply text (TTS) | [`formatters/voice.py`](../src/advisor_scheduler/formatters/voice.py) (`format_for_voice`, `build_tts_text`) — used by `POST /chat` voice formatting and `POST /voice-turn` server TTS |
| Deepgram STT/TTS adapter | [`integrations/deepgram.py`](../src/advisor_scheduler/integrations/deepgram.py) |
| Session state names | [`llm/response_schema.py`](../src/advisor_scheduler/llm/response_schema.py) (`AllowedState`) and [`core/engine.py`](../src/advisor_scheduler/core/engine.py) |
| Heuristic intent classification | [`intents/router.py`](../src/advisor_scheduler/intents/router.py) → [`types/models.py`](../src/advisor_scheduler/types/models.py) `Intent` |
| Compliance (PII / investment advice) | [`guards/compliance.py`](../src/advisor_scheduler/guards/compliance.py) — runs **before** the engine on every turn; **not** produced by `route_intent()` |
| Turn processing | [`core/engine.py`](../src/advisor_scheduler/core/engine.py) `process_message` — deterministic path first, then Gemini JSON (`GeminiTurnDecision`) when needed |
| Topics | [`core/topics.py`](../src/advisor_scheduler/core/topics.py) |
| Mock slot grid (no MCP) | [`services/slot_service.py`](../src/advisor_scheduler/services/slot_service.py) `_MOCK` |
| Booking codes & memory | [`services/booking_service.py`](../src/advisor_scheduler/services/booking_service.py) |
| Calendar / Sheets / Gmail | [`integrations/factory.py`](../src/advisor_scheduler/integrations/factory.py) — stubs vs MCP |
| MCP tools (live Google) | [`integrations/google_workspace/server.py`](../src/advisor_scheduler/integrations/google_workspace/server.py) |
| Downstream booking actions | [`orchestration/side_effects.py`](../src/advisor_scheduler/orchestration/side_effects.py) |
| Environment | [`config.py`](../src/advisor_scheduler/config.py), [`.env.example`](../.env.example) |

## HTTP surface (from code)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/chat` | JSON `session_id`, `message`, optional `channel` (`chat` default, `voice` for speakable formatting) → assistant reply and `session_state` |
| `POST` | `/voice-turn` | Raw audio request body plus `session_id` query param → Deepgram STT, existing engine turn, Deepgram TTS, transcript + assistant payload + base64 audio |
| `GET` | `/` | Serves chat UI (`api/static/index.html` via FastAPI; **Vercel** also mirrors the same HTML at [`public/index.html`](../public/index.html) for edge delivery) |
| `GET` | `/secure-details` | Demo static page for secure-link landing (`api/static/secure-details.html`; **Vercel** mirror: [`public/secure-details/index.html`](../public/secure-details/index.html)) |
| `GET` | `/health` | `{"status": "ok"}` |
| — | `/static/*` | Static assets |

### Vercel (production hosting)

- **ASGI entry:** [`api/index.py`](../api/index.py) re-exports `app` from [`advisor_scheduler/api/app.py`](../src/advisor_scheduler/api/app.py) so the Vercel builder detects FastAPI.
- **Packaging:** [`pyproject.toml`](../pyproject.toml) declares `[project.scripts] app = "advisor_scheduler.api.app:app"` and `[tool.setuptools.package-data]` for `api/static/*.html` inside the installed package.
- **Operator:** set `PUBLIC_BASE_URL` and secrets in the Vercel project; see repo root [README.md](../README.md). In-memory sessions may not match a single long-lived local server across invocations.

## Session states (actual strings in code)

Aligned with `AllowedState` in [`response_schema.py`](../src/advisor_scheduler/llm/response_schema.py):

`greeting`, `identify_intent`, `collect_topic`, `collect_time`, `offer_slots`, `confirm_slot`, `offer_waitlist`, `collect_code`, `collect_time_reschedule`, `offer_slots_reschedule`, `confirm_slot_reschedule`, `collect_code_cancel`, `confirm_cancel`, `collect_topic_prepare`, `show_guidance`, `collect_day`, `show_availability`, `closing`, `idle`.

**Not** separate named states: booking/reschedule/cancel **execution** and code **validation** happen inside handlers / one turn; the LLM schema uses **actions** such as `execute_booking`, `execute_reschedule` — see `AllowedAction` in `response_schema.py`.

## Intents: router vs LLM schema

- **`route_intent()`** returns: `book_new`, `reschedule`, `cancel`, `what_to_prepare`, `check_availability`, `unknown` (and confidence).  
- **`Intent` enum** also includes `share_pii` and `ask_investment_advice` for typing elsewhere; **compliance** handles PII/advice blocking in practice.  
- **Gemini structured turn** uses `AllowedIntent`: `book_new`, `reschedule`, `cancel`, `what_to_prepare`, `check_availability`, `unknown` only.

## Phases vs this repository

| Phase | What the code does today |
|-------|---------------------------|
| **1** | Chat-first engine, `POST /chat`, in-process **stubs** when `use_mcp=false`, deterministic mock slots from `_MOCK` |
| **2** | Same codebase; when `use_mcp=true` and IDs/credentials are set, **FastMCP** adapters talk to Google (Calendar, Sheets, Gmail) per [`mcp_contracts.md`](mcp_contracts.md) |
| **3** | **Voice:** browser `MediaRecorder` in [`api/static/index.html`](../src/advisor_scheduler/api/static/index.html) sends audio to `POST /voice-turn`; server uses Deepgram STT/TTS and the existing text engine, while `format_for_voice` / `build_tts_text` remain the spoken-text source of truth. |

## What is still “pending” (easy language)

See the end of [`docs/README.md`](README.md) after this sync.
