# Architecture: Advisor Appointment Scheduler

## Goal
Build a low-cost project prototype for an advisor appointment scheduler that starts with chat and later supports voice, while preserving the same core business logic.

## Core Architecture Principle
The system should be channel-agnostic at its core.

That means:
- the same conversation engine should power both chat and voice
- chat and voice should differ only in input/output adapters and response formatting
- business logic should not depend on FastMCP, a specific speech vendor, or any channel-specific implementation (integrations stay behind adapters and settings)

## Voice Design Principles
When voice is added later:
- the same conversation engine should be reused
- STT and TTS should remain isolated behind adapters
- the system should assume transcripts may be imperfect
- confirmation should be required before committing booking actions
- voice responses should be optimized for listening, not reading
- users should be able to correct or revise topic and time preference without restarting the entire flow

## Phase-wise Architecture Strategy

### Phase 0: Foundation
Purpose:
- define product constraints
- define folder structure
- define architecture
- define contracts
- define rules
- define phases

Outputs:
- docs
- Cursor rules
- source folder skeleton
- integration boundaries

No production logic should be written in this phase.

### Phase 1: Chat-first Core Flow
Purpose:
- implement the conversation engine
- implement supported intents
- implement compliance guardrails
- implement deterministic mock slot offering when MCP is off (`SlotService._MOCK` in code)
- implement booking creation logic
- implement response formatting for chat
- implement lightweight tests

This phase should not include real integrations.

### Phase 2: MCP Integration for Google Workspace Actions
Purpose:
- connect the app to Google Calendar
- connect the app to Google Sheets
- connect the app to Gmail
- keep all integrations behind adapters (`CalendarAdapter`, `SheetsAdapter`, `GmailAdapter` protocols)
- support calendar hold creation, booking row append, and email draft creation
- toggle real MCP calls with `use_mcp` in settings; when `False`, Phase 1 stubs remain in use for local development and CI

Implementation notes:
- `build_adapters(settings)` in `integrations/factory.py` returns either stubs or FastMCP-backed `CalendarMcpAdapter`, `SheetsMcpAdapter`, and `GmailMcpAdapter`, all sharing a single client transport
- The MCP transport is resolved by `load_mcp_client_source()` in `integrations/google_workspace/mcp.py`. By default it launches the **in-repo Python FastMCP server** (`integrations/google_workspace/server.py`) which exposes `calendar_create_hold`, `calendar_update_hold`, `calendar_delete_hold`, `calendar_get_freebusy`, `sheets_append_prebooking`, `sheets_list_prebookings`, and `gmail_create_draft`. Set `MCP_GOOGLE_CONFIG` to override with a JSON file (e.g., the one written by `scripts/setup_google_mcp.py`), inline JSON, or any form FastMCP accepts; see `docs/mcp_contracts.md` for payload shapes
- `SlotService` uses Google Calendar free/busy via MCP when `use_mcp=True` and `google_calendar_id` is set; otherwise it keeps the deterministic mock schedule from Phase 1

Local development and CI can keep `use_mcp=False` with stubs; enable real MCP when configs and credentials are ready (see `docs/GOOGLE_MCP_QUICKSTART.md`).

### Phase 3: Voice (MVP shipped)
Implemented purpose:
- **Browser `MediaRecorder`** in `api/static/index.html`: hold-to-talk audio capture uploads raw audio to `POST /voice-turn`; **barge-in** stops in-progress playback when the user starts recording again; **Stop speaking** stops HTML audio playback.
- **Server-owned Deepgram voice adapter** in `integrations/deepgram.py`: FastAPI calls Deepgram STT before `process_message(...)`, then Deepgram TTS on `tts_text`.
- **Voice-oriented response formatting** in `formatters/voice.py` (`format_for_voice`, `build_tts_text`): keeps spoken cleanup in one place while the engine and session logic stay shared with chat.
- **Not in this phase:** websockets/streaming audio, wake word, native apps.

**Current code:** typed chat remains **text-in / text-out** on `POST /chat`; voice uses `POST /voice-turn` as a backend-proxied audio turn. Mic access still needs a **secure context** (HTTPS or localhost); Chrome desktop remains the reference browser.

## High-Level Modules

### 1. Intent Router
Responsibility:
- map user input to one of the supported intents or guardrail/fallback classifications
- identify unsupported or ambiguous cases

Supported product intents:
- book_new
- reschedule
- cancel
- what_to_prepare
- check_availability

The **heuristic router** (`route_intent`) classifies product intents and `unknown`. It does **not** emit PII or investment-advice labels; those are handled by the **compliance guard** (`compliance_guard`) before the engine runs.

The `Intent` enum in `types/models.py` includes `share_pii` and `ask_investment_advice` for typing consistency; in practice **compliance** blocks those patterns on every turn.

### 2. Conversation Engine
Responsibility:
- maintain conversation state
- decide next step in the flow
- coordinate user interaction based on state and intent
- stay independent from chat or voice specifics

This is the heart of the app.

### 3. Compliance Guard
Responsibility:
- enforce no-PII rule
- detect and interrupt sharing of sensitive information
- refuse investment advice requests
- enforce allowed-topic behavior
- ensure confirmation contains IST and repeated slot details

The compliance guard runs on every incoming user turn, regardless of current conversation state. It is not a one-time gateway at session start.

### 4. Slot Service
Responsibility:
- accept time preference and optional topic
- return matching slots (up to two)
- return no-slot result when nothing matches

Topic is optional for availability checks and required for actual booking. All slots are 30 minutes.

In Phase 1 this is mocked and deterministic. In Phase 2, when `use_mcp` is enabled and MCP settings are present, availability is derived from Calendar free/busy for the configured advisor hours (IST).

### 5. Booking Service
Responsibility:
- generate booking code (format `NL-XXXX`, uniqueness enforced)
- construct booking object
- store booking record
- lookup booking by code
- update booking status and details
- assign booking status
- prepare downstream payloads

For Phase 1, booking storage starts with an in-memory dict keyed by booking code. When the Sheets adapter is available, booking-code lookup can also reconstruct the latest persisted state from the append-only event log, with the in-memory store acting as a local cache and code generator. Bookings persist for the lifetime of the process and survive session timeouts.

Normalized statuses:
- tentative
- waitlisted
- rescheduled
- cancelled
- failed

### 6. Orchestration Layer
Responsibility:
- connect conversation engine decisions to service calls
- call downstream integration adapters
- manage success and failure behavior
- centralize booking completion logic

Phase 1 note: it is acceptable to implement orchestration as a single `execute_side_effects(booking)` function called by the conversation engine. Extract to a dedicated module when retry, rollback, or fallback logic is needed in later phases.

### 7. Formatters
Responsibility:
- format output for the target channel

**Current code:** responses are built inline in `core/engine.py` (and helpers such as `_wrap`). **`formatters/voice.py`** supplies `format_for_voice` for the voice channel only; it performs string cleanup (lists, markdown bold) with no business logic.

## Formatting Layer Responsibilities
Formatting should be kept separate from business logic.

Chat formatter responsibilities:
- produce concise readable text responses
- support clear confirmations and guidance

Voice formatter responsibilities (Phase 3):
- shorten phrasing for spoken delivery
- reduce list-heavy output
- present slot options in a way that is easy to hear once
- support confirmation-first wording for important details

Neither formatter should contain booking logic, state transitions, or integration logic.

### 8. Integration Adapters
Responsibility:
- isolate external systems behind clean interfaces

Groups:
- Google Workspace adapters via FastMCP (implemented under `integrations/google_workspace/`)
- Speech: **browser-only** STT/TTS in `api/static/index.html`; no server-side speech adapters

## Conversation State Design Guidance
The conversation engine should support:
- explicit state transitions
- correction turns without full restart
- repeat-last-response behavior
- repeat only the relevant structured state when possible (slot options, pending confirmation, or availability summary) instead of blindly replaying long text
- clarification turns for ambiguous date/time input
- graceful fallback when no valid slot or booking code is available
- session-level greeting behavior rather than flow-level repeated greetings
- intent switching mid-session through transition states rather than restarting the session

The state machine should be designed so that chat and voice both reuse the same underlying states.

## Session Management

### Session Definition
For Phase 1, a session is:
- an in-memory conversation state object
- keyed by a client-provided `session_id`
- stored in process memory (no external persistence)

### Session Timeout
- sessions expire after 20 minutes of inactivity
- when a session times out, conversation state is cleared and the next user message starts a new session with a fresh greeting
- booking records already created are not affected by session timeout; they persist in the booking store independently

### Session Behavior Rules
The system should distinguish between:
- session start behavior
- mid-session flow transitions

Guidelines:
- greeting should be treated as a session-level state, not a per-flow state
- a new greeting should be used only at the start of a new session
- if the user changes intent during an active session, the system should use a short transition instead of greeting again
- the disclaimer may be repeated when necessary, but not automatically on every flow switch

## Integration Boundaries

### Google Workspace via FastMCP
Adapters should be created for:

#### Calendar Adapter
Used to create tentative holds.

Responsibilities:
- accept hold request payload
- call FastMCP tool/server
- return structured success/failure result

#### Sheets Adapter
Used to append booking records.

Responsibilities:
- accept booking row data
- append to the `Advisor Pre-Bookings` sheet/tab
- return structured success/failure result

#### Gmail Adapter
Used to create advisor-facing drafts.

Responsibilities:
- accept draft payload
- prepare approval-gated email draft
- return structured success/failure result

### Speech (Phase 3, browser MVP)
The browser records audio and sends it to `POST /voice-turn`. FastAPI transcribes with Deepgram STT, sends the transcript through the same conversation engine, shapes spoken text with `format_for_voice` / `build_tts_text`, synthesizes Deepgram TTS, and returns both assistant JSON and audio. The conversation engine and state machine remain unchanged.

## Data Flow

The following pipeline runs on every incoming user turn. The compliance guard is not a one-time check; it filters every message regardless of current conversation state.

### Chat Flow (per turn)
User text input
→ compliance guard (every turn)
→ append user message to session history
→ optional mid-flow intent switch (heuristic router when user changes intent during a new-booking flow)
→ **deterministic handlers** (greeting and high-confidence intents, topic match, day/time parse, offered-slot choice, code collection, waitlist yes/no, execute confirmations, closing replies—see state machine note below)
→ **if no handler returned a response:** Gemini JSON decision (compact prompt: compressed JSON context, last **4** history turns, optional intent hint from `route_intent`)
→ conversation engine applies validated decision and server-side slot/orchestration logic
→ services (if needed)
→ integration adapters (if needed)
→ assistant response text (`ChatResponse`)
→ user

### Voice Flow (Phase 3, per turn)
User audio
→ browser `MediaRecorder`
→ `POST /voice-turn` (raw audio body + `session_id`)
→ **Deepgram STT**
→ compliance guard (every turn)
→ same engine pipeline as chat (deterministic steps first, then Gemini only when needed)
→ services (if needed)
→ integration adapters (if needed)
→ assistant `response` text
→ **`format_for_voice` / `build_tts_text`** (server, voice channel only)
→ **Deepgram TTS**
→ base64 audio in HTTP response
→ user hears spoken reply

## HTTP API (FastAPI: `src/advisor_scheduler/api/app.py`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | Conversation turn: JSON body below |
| `POST` | `/voice-turn` | Voice turn: raw audio request body plus `session_id` query param |
| `GET` | `/` | Chat web UI (`api/static/index.html`) |
| `GET` | `/secure-details` | Static demo page for secure-link landing (`secure-details.html`) |
| `GET` | `/health` | Health check `{"status": "ok"}` |
| `GET` | `/static/...` | Static assets |

### Deployment (Vercel)

The same FastAPI app can run on **[Vercel](https://vercel.com/docs/frameworks/backend/fastapi)** using [`api/index.py`](../api/index.py) as the detected entrypoint (re-export of `advisor_scheduler.api.app:app`). Static HTML is duplicated under [`public/`](../public/) (`index.html`, `secure-details/index.html`) so the platform can serve the UI from the CDN; keep those files in sync when editing [`api/static/`](../src/advisor_scheduler/api/static/).

Configure **`PUBLIC_BASE_URL`**, **`GEMINI_API_KEY`**, **`DEEPGRAM_API_KEY`**, and Google/MCP variables in the Vercel project to match production. Expect **serverless** behavior: in-memory **session** state is not guaranteed to stick to one instance across requests; allow enough **function duration** for `/voice-turn` (STT + engine + TTS). Details and caveats are in the repo root [README.md](../README.md).

### `POST /chat`

**Request**
```json
{
  "session_id": "string",
  "message": "string",
  "channel": "chat"
}
```

- `session_id`: client-provided identifier; used to look up or create session state
- `message`: the user's text input for this turn
- `channel`: optional; `"chat"` (default) or `"voice"`. When `"voice"`, the assistant `response` is passed through `format_for_voice` before returning (shorter, speakable text). The engine sees the same `message` either way.

**Response**
```json
{
  "response": "string",
  "session_state": "string",
  "booking_code": "string or null",
  "secure_link": "string or null",
  "status": "string or null"
}
```

- `response`: the assistant's text reply (voice-formatted when the request used `channel: "voice"`)
- `session_state`: current named state (see `AllowedState` in `llm/response_schema.py`)
- `booking_code`: present when relevant to this session
- `secure_link`: when the secure details URL is valid and not placeholder-like (see settings)
- `status`: booking status when relevant (e.g. `tentative`, `waitlisted`)

### `POST /voice-turn`

**Request**
- query: `session_id=...`
- body: raw recorded audio bytes (`audio/webm` in the browser UI when supported)

**Response**
```json
{
  "transcript": "string",
  "response": "string",
  "tts_text": "string or null",
  "session_state": "string",
  "booking_code": "string or null",
  "secure_link": "string or null",
  "status": "string or null",
  "audio_base64": "string or null",
  "audio_mime_type": "string or null"
}
```

- `transcript`: Deepgram STT output for the uploaded turn
- `response`: assistant text shown in the UI
- `tts_text`: spoken-safe assistant text after link/code cleanup
- `audio_base64`: Deepgram TTS audio when synthesis succeeds
- `audio_mime_type`: MIME type for the returned audio payload

---

## Conversation Engine Implementation

### Default LLM
Phase 1 uses Gemini 2.5 Flash as the default runtime model. This is chosen for its free tier and low cost, suitable for a prototype.

The provider should be configurable so it can be swapped later. Groq 8B is noted as an optional future alternative but is not the default.

### Prompting Approach (Gemini fallback only)
When deterministic handlers do not return a reply, the engine calls Gemini with a **compact** payload built in `src/advisor_scheduler/llm/prompt_builder.py`:
- **SYSTEM_PROMPT**: role, compliance, IST, allowed topics, JSON-only output
- **Compact schema line**: enumerates allowed states, intents, and actions in one short string (no duplicated verbose per-field prose)
- **Context JSON** (minified): `topics` (allowed list only), a small **intent hint** from `route_intent()`, abbreviated session snapshot (`s`), and **last 4** chat turns (the current user line appears only in this history slice—not duplicated as a separate `latest_user_message` field)

This reduces input tokens versus earlier designs that duplicated the latest message, repeated a separate topics menu string, sent 12 history turns, and indented the full JSON.

### State Injection (Gemini fallback)
On a Gemini turn:
1. the prompt includes the current structured session fields (topic, dates, offered slots, etc.) inside the compact context object
2. the last four history entries provide recent dialogue without re-sending the full transcript
3. Gemini returns structured JSON (`GeminiTurnDecision`)
4. the engine validates the payload before mutating session state or triggering side effects

For turns handled **without** Gemini, the engine advances state directly from heuristics and service results (slot matching, booking creation, etc.).

The conversation engine is responsible for updating named state after each turn, whether the path was deterministic or LLM-driven.

### Structured Response Contract
Gemini returns JSON with fields such as:
- `reply`
- `next_state`
- `intent`
- `action`
- `topic`
- `requested_day_text` (legacy phrase; deterministic parse when present)
- `resolved_day_iso` (preferred: a single `YYYY-MM-DD` in IST semantics when the model resolves a day)
- `time_window`
- `selected_slot_index`
- `booking_code`
- `close_session`

This response is validated server-side before any booking, reschedule, cancel, waitlist, or availability action is executed.

### Guardrails Around Gemini
Gemini does **not** drive every turn. When it is invoked, deterministic enforcement still applies:
- the compliance guard runs on every user turn before any model call
- unsupported topics or malformed transitions are rejected by a transition validator
- booking, waitlist, reschedule, and cancel execution still require an explicit affirmative user turn (or deterministic execution paths that mirror the same rules)
- downstream calendar, sheets, and Gmail work remains behind adapters and is only triggered from validated execute actions

---

## Phase 1 State Machine

The conversation engine uses named states to track progress through each flow. States are shared across chat and voice channels.

> **Implementation note:** Phase 1 uses a *hybrid* engine. **Deterministic handlers run first** and cover most of the happy path, including: greeting and `identify_intent` when intent heuristics are confident; `collect_topic` via keyword/topic rules; `collect_time` / `collect_time_reschedule` / `collect_day` when `resolve_user_day()` (deterministic `parse_day_token` first, then structured day-resolution JSON from Gemini if needed) and `infer_time_window` succeed; offered-slot selection (time/ordinal phrasing); lightweight topic/day/time/slot corrections; booking-code collection with retries; waitlist yes/no; repeat requests that replay structured state; confirmation before side effects; broader mid-flow intent switches; and `closing` carry-through when the user immediately asks for another supported task. **Gemini 2.5 Flash** is a **fallback** for turns that need natural language plus structured JSON when heuristics return no decision. This minimizes quota usage while keeping compliance-critical actions outside purely free-form model output.
>
> The states `execute_booking`, `execute_waitlist`, `execute_reschedule`, `execute_cancel`, `validate_code`, and `share_result` listed in early design notes are **not discrete named states** in the implementation — they are *actions* within the same turn that execute inline and then transition to `closing`. `validate_code` / `validate_code_cancel` are likewise folded into the `collect_code` / `collect_code_cancel` handler rather than being separate state nodes.

**Authoritative list:** `AllowedState` in `src/advisor_scheduler/llm/response_schema.py` matches the strings used in `core/engine.py`.

### Named states (session + flows)

- **Session / routing:** `greeting` → `identify_intent`; after a clean goodbye the session may be `idle` (next user message re-enters `identify_intent`).
- **New booking:** `collect_topic` → `collect_time` → `offer_slots` → `confirm_slot` → `closing` (after success, booking executes **in the same turn**, not a separate state named `execute_booking`).
- **Waitlist:** `offer_waitlist` → on yes, waitlist side effects run **inline** → `closing`; on no, typically `collect_time` to pick another day.
- **Reschedule:** `collect_code` → `collect_time_reschedule` → `offer_slots_reschedule` → `confirm_slot_reschedule` → `closing`. Code validation happens **while** in `collect_code`, not a state named `validate_code`.
- **Cancel:** `collect_code_cancel` → `confirm_cancel` → `closing`. Same note on validation inside `collect_code_cancel`.
- **Availability:** `collect_day` → `show_availability` (can branch into booking).
- **Prepare:** `collect_topic_prepare` → `show_guidance` (can branch into booking).

### LLM `action` values (not session states)

`AllowedAction` in `response_schema.py` includes `execute_booking`, `execute_waitlist`, `execute_reschedule`, `execute_cancel`, etc. These are **structured outputs** for Gemini turns, not separate **named states** in the engine.

### Intent Switching
When the user changes intent mid-session:
- the engine transitions to the entry state of the new flow
- session-level context (greeting delivered, disclaimer delivered) is preserved
- flow-specific state (topic, time, slots) may be carried over or cleared depending on the new intent
- the assistant uses a short transition statement, not a new greeting

### Retry Behavior
- invalid booking code: allow up to 3 attempts, then suggest double-checking the code or starting a new booking
- after max retries, transition to `closing`
- if the user asks for another supported task while in `closing`, dispatch directly into that flow without forcing them to repeat the request

---

## Mock Availability Data

For Phase 1 testing, the slot service uses a fixed deterministic dataset. This ensures repeatable test scenarios.

### Mock Slots

| Day       | Time (IST)  | Available |
|-----------|-------------|-----------|
| Monday    | 10:00-10:30 | yes       |
| Monday    | 14:00-14:30 | yes       |
| Tuesday   | 11:00-11:30 | yes       |
| Wednesday | 10:00-10:30 | yes       |
| Wednesday | 15:00-15:30 | yes       |
| Thursday  | 14:00-14:30 | yes       |

### Mock Behavior
- when the user requests a day with available slots, return up to two matching slots
- when the user requests a day with no slots (e.g. Friday, Saturday, Sunday), return no results and trigger the waitlist flow
- the mock data is relative to the current week for display purposes

### Date and time window parsing
Before slot matching, `resolve_user_day()` in `slot_service.py` runs `parse_day_token()` on the user message first. That deterministic path understands `today`, `tomorrow` (word-boundary match so it does not mistake `day after tomorrow` for `tomorrow`), `day after tomorrow` / `in two days`, weekday names (next occurrence), explicit dates such as `25th April` or `April 25`, numeric `DD/MM` (or `DD/MM/YYYY`) forms, and phrasing such as **after** / **on or after** / **before** adjacent to an explicit date. If the year is omitted, dates in the past relative to “today” in IST roll to the next year. Multiple conflicting day signals produce an ambiguous result and trigger the next step.

When deterministic resolution does not yield a single day, the engine calls Gemini once with a **structured** day-resolution JSON contract (`resolved_date_iso`, `is_ambiguous`, optional `reason` / `normalized_time_window`) so the model returns a validated `YYYY-MM-DD` instead of a free-form phrase that would be parsed again. The same resolver path is used for booking, reschedule, and availability. On full Gemini turn decisions, `resolved_day_iso` is preferred over `requested_day_text` when both are present.

Time-of-day words (`morning`, `afternoon`, `evening`) are handled separately via `infer_time_window()` for filtering mock or MCP-backed slots.

If no day can be resolved after both steps, the user is asked for a clearer day and time window in IST.

---

## Folder Design Principle
Code should be organized by responsibility, not by phase.

Good (actual layout under `src/advisor_scheduler/`):
- `core/`, `guards/`, `intents/`, `services/`, `orchestration/`, `llm/`, `integrations/`, `api/`, `formatters/`, `types/`

Avoid:
- src/phase_1
- src/phase_2
- src/phase_3

Phases should live in docs and planning, not in runtime architecture.

## Stubs and testability
To keep the prototype cost-effective:
- with `use_mcp=false`, **in-process stubs** implement `CalendarAdapter`, `SheetsAdapter`, `GmailAdapter` (`integrations/google_workspace/stubs.py` via `factory.py`)
- the slot service uses **`_MOCK` weekday grid** when MCP free/busy is not used
- booking side effects remain testable without real Google APIs
- speech / voice: browser audio capture in the static chat UI plus Deepgram STT/TTS behind `integrations/deepgram.py`; typed chat still uses `POST /chat`

## Source folder (as implemented)

```
src/advisor_scheduler/
  api/              # FastAPI app + static chat UI (includes Phase 3 voice controls)
  formatters/       # voice-oriented text (e.g. format_for_voice)
  core/             # engine, session, topics
  guards/           # compliance
  intents/          # heuristic router
  integrations/     # factory, google_workspace (MCP + stubs)
  llm/              # Gemini client, prompts, response schema
  orchestration/    # side effects
  services/         # slots, bookings
  types/
tests/
```

## Key Design Rules
- keep the conversation engine channel-agnostic
- keep business logic separate from formatting
- keep external integrations behind adapters
- prefer deterministic mocks before real API calls
- avoid unnecessary repo-wide refactors
- optimize for low cost and easy debugging
- preserve compliance constraints in all layers

## Early Testing Strategy
The prototype does not need a heavy eval framework.

It should support:
- lightweight transcript-based test scenarios
- unit tests for compliance guards
- unit tests for slot matching
- unit tests for booking generation
- basic orchestration tests with integration stubs

## Definition of Done for Architecture
The architecture is satisfied in this repo when:
- module boundaries match the layout above
- FastMCP Google adapters are behind `integrations/` and `use_mcp`
- chat-first strategy is preserved; typed chat stays on `POST /chat`, while voice adds `POST /voice-turn` and still reuses the same engine and voice formatter
- compliance and adapter contracts remain enforceable