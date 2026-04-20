# Voice Agent: Advisor Appointment Scheduler

## Overview
Build a compliant advisor appointment scheduler that is implemented chat-first and later extended to voice using the same core workflow, with channel-specific input/output adapters and response formatting.

The system should help a user schedule a tentative advisor appointment by:
- understanding the reason for the consultation
- collecting preferred day and time window
- offering available slots
- confirming the selected slot
- triggering the required downstream actions via MCP integrations
- returning a booking code and a secure link where the user can submit contact details separately, outside the chat or voice interaction

This is a project prototype. Early phases prioritize cost-effective **adapter stubs** (when `USE_MCP=false`), deterministic mock slots from `SlotService._MOCK`, and limited external API usage. **Code in `src/advisor_scheduler/` is the source of truth**; see [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md).

## Conversation engine: deterministic first, LLM fallback
The runtime engine runs **heuristic handlers first** on every turn (compliance guard, intent hints, topic matching, date/time parsing, slot selection from offered options, booking-code collection, waitlist yes/no, confirmations, closing).  

When those handlers can fully decide the next step, **no Gemini call is made**, which keeps latency down and reduces API quota usage.

**Gemini 2.5 Flash** is used only when deterministic logic does not apply—for example ambiguous wording, edge intents, or turns that need natural-language replies while still returning validated structured JSON.

## Build Strategy
The product should be built in phases:
- Phase 0: foundation, architecture, rules, folder structure, and contracts
- Phase 1: chat-first flow
- Phase 2: MCP integration for Google Workspace actions
- Phase 3: voice (STT/TTS + optional spoken-friendly formatting) — **not implemented in this repo**; browser Web Speech API or cloud providers would sit in front of the same `POST /chat` text API

The core conversation engine should remain channel-agnostic so that both chat and voice reuse the same business workflow.

## Supported User Intents
The assistant must support the following intents:
1. Book a new advisor appointment
2. Reschedule an existing advisor appointment
3. Cancel an existing advisor appointment
4. Ask what to prepare before the consultation
5. Check advisor availability windows

## Core User Flow for New Booking
1. Greet the user
2. State the disclaimer:
   "This conversation is for informational support only and does not provide investment advice."
3. Identify or confirm the user intent
4. Confirm the consultation topic from the allowed list
5. Collect preferred day and time window
6. Offer two available slots from the availability service
7. Ask the user to choose one slot
8. Repeat the selected date and time clearly in IST
9. Ask for final confirmation
10. On confirmation:
    - generate a booking code
    - create a tentative calendar hold
    - append a booking row to the Google Sheets tab `Advisor Pre-Bookings`
    - prepare an advisor email draft
11. Return the booking code
12. Share a secure URL where the user can submit contact details separately, outside the chat or voice interaction

## Conversation Design Principles
The assistant should be designed for low-friction, high-clarity interactions.

Guidelines:
- ask only one important question at a time
- avoid long multi-part responses
- do not overload the user with too many choices
- use explicit confirmations before any booking-related action
- allow users to correct topic or timing without restarting the whole flow
- prefer concise, natural language over formal or verbose responses

## Allowed Topics
The user must choose from one of these supported consultation topics:
- KYC / Onboarding
- SIP / Mandates
- Statements / Tax Docs
- Withdrawals / Timelines
- Account Changes / Nominee

If the user provides an unsupported topic, the assistant should guide them back to the supported list.

## Compliance Rules
The assistant must follow these rules at all times:
- no PII should be collected in the conversation
- the assistant must not ask for:
  - phone number
  - email address
  - account number
  - PAN
  - Aadhaar
  - any personal identifier
- if the user voluntarily shares PII, the assistant must interrupt politely and redirect them
- the assistant must not provide investment advice
- if the user asks for investment advice, the assistant must refuse and redirect to educational help or booking support
- all confirmed times must be stated in IST
- the final selected slot must always be repeated before confirmation

## Booking Code
A booking code must be generated for all booking-related flows.

Format:
`NL-XXXX`

Example:
`NL-A742`

The exact generation strategy uses in-process logic (`BookingService`) as long as the format remains consistent.

Booking code uniqueness must be enforced even in mock mode. The generation logic must check against existing codes and never produce duplicates.

## Slot Configuration
All advisor slots are 30 minutes.

## Date and time window resolution
Day resolution uses one shared path in code (`resolve_user_day` in `services/slot_service.py`):

1. **Deterministic parse** via `parse_day_token` (and `infer_time_window` for morning/afternoon/evening) when they yield a single unambiguous IST calendar day.
2. **Structured Gemini fallback** when step 1 does not resolve: a dedicated prompt returns JSON with `resolved_date_iso`, `is_ambiguous`, and optional `normalized_time_window` — no re-parsing of free-form normalized phrases.
3. **Full turn (GeminiTurnDecision)** may set `resolved_day_iso` or legacy `requested_day_text`; `resolved_day_iso` is preferred when both appear.

Booking, reschedule, and availability all use the same resolver entry point.

**Supported patterns (non-exhaustive):**
- **Relative:** `today`, `tomorrow`, `day after tomorrow` (must be recognized as +2 days; the phrase contains the word “tomorrow” and is handled explicitly), `in two days`, `two days from now`
- **Weekday:** the next occurrence of that weekday from “now” (e.g. `Monday`, `next Tuesday`)
- **Explicit calendar dates:** e.g. `25th April`, `April 25`, `25/04` (optional four-digit year)
- **Time-of-day window:** `morning`, `afternoon`, `evening` (and common variants such as `am` / `pm` where applicable) combined with the day text

**Year handling:** If the user does not specify a year, the engine uses the current calendar year when that date is still on or after “today” (IST); otherwise it rolls forward to the same month and day in the next year. If the user explicitly provides a year and the resolved day is before today in IST, the request is rejected as a past date instead of being silently accepted or sent to Gemini as a normal availability query.

**Past-date guard:** The same not-in-the-past validation applies across deterministic parsing, the dedicated Gemini day-resolution path, and full-turn Gemini decisions that set `resolved_day_iso`. Booking, reschedule, and availability flows should all ask the user for a day from today onward in IST when a past day is detected.

**Offered-slot selection:** When slots are already on screen, the engine should try to match the user's reply to one of those offered slots before treating the message as a correction to the requested day or time window. This includes exact phrases such as `27th April 3:30 PM`, `12:30`, `second option`, or `the later one`.

All offered and confirmed times are expressed in **IST**, consistent with the rest of the product.

## MCP Integration Requirements
Phase 2 uses MCP (via FastMCP) for Google Workspace actions when `USE_MCP=true` and MCP transports are configured. Phase 3 (voice) is **not** part of the Python package yet; it would add client-side speech and still call `POST /chat` with text.

### Google Workspace via MCP
When enabled, MCP integrations perform:
- Google Calendar tentative hold creation, update (reschedule), and delete (cancel when a hold id exists)
- Google Sheets booking rows in `Advisor Pre-Bookings` (append-only event log; the in-repo FastMCP `sheets_append_prebooking` tool finds the next free row from column A and writes an explicit `A{row}:P{row}` range so columns stay aligned with the shared 16-column schema)
- Google Sheets read-back via `sheets_list_prebookings` so reschedule/cancel can reconstruct the latest state per `booking_code` from the log when needed
- Gmail draft creation (internal draft only; no auto-send; sender mailbox from environment config)

Gmail drafts are internal to the configured mailbox. The assistant must never ask the user for an email address. No emails are auto-sent in Phase 1.

With `USE_MCP=false`, the app uses in-process stubs for the same adapter interfaces (suitable for CI and local flow testing without live Google MCP).

### Voice (Phase 3 — future)
Not implemented under `src/advisor_scheduler/` today. Options include **browser Web Speech API** (client-only STT/TTS) or **cloud** STT/TTS (e.g. Deepgram, ElevenLabs, others). In all cases the core engine should receive **text** only via `POST /chat` and return **text** responses.

## Booking Success Behavior
When a slot is successfully selected and explicitly confirmed:
- generate booking code
- create tentative calendar hold
- append a booking row to the Google Sheets tab `Advisor Pre-Bookings`
- prepare advisor-facing email draft (internal draft only, no auto-send)
- return booking code
- provide a secure link for collecting contact details outside the conversation
- ask the user if there is anything else they need help with
- if not, end with a short closing message

## Waitlist Behavior
If no suitable slots are found:
- inform the user that no matching slots are available right now
- offer to place the user on the waitlist
- wait for explicit user consent before proceeding
- on consent:
  - generate a booking code
  - create a waitlist record (append to Google Sheets with status `waitlisted`)
  - prepare a waitlist advisor email draft
  - provide the secure details link
  - share the booking code and waitlist status
  - ask the user if there is anything else they need help with
  - if not, end with a short closing message
- keep the experience graceful and clear

### Waitlist Decline
If the user declines the waitlist:
- do not create any records
- do not generate a booking code
- offer to search for a different day or time window
- if the user does not want to try again, end with a short closing message

## Reschedule Behavior
For reschedule:
- ask only for booking code
- do not ask for personal details
- validate booking code
- collect new preferred day and time window
- offer two alternative slots if available
- repeat the selected date and time in IST
- ask for final confirmation
- update downstream records accordingly only after confirmation

## Cancel Behavior
For cancel:
- ask only for booking code
- validate booking code
- confirm cancellation intent
- update downstream records accordingly only after confirmation

## What-to-Prepare Behavior
When the user asks what to prepare:
- provide generic preparation guidance relevant to the selected topic
- do not ask for or collect personal details
- do not give investment advice
- optionally offer to help with booking

## Availability Windows Behavior
When the user asks for availability windows:
- collect preferred day if needed
- topic is not required for availability checks in Phase 1
- return available windows in IST
- optionally transition into booking flow if the user wants to proceed

## Secure Link Behavior
The secure link is for collecting contact details outside the conversation.

The conversation itself must not collect contact details.

The secure link uses a configured HTTPS base URL read from the environment variable `SECURE_DETAILS_BASE_URL`. The assistant appends the booking code as a query parameter. Placeholder/example-style URLs should be treated as invalid and should not be shown to the user.

Example:
`https://secure.nextleap.example/details?code=NL-A742`

The contact form or page behind this URL is out of scope for Phase 1. The assistant only returns the constructed URL string.

The secure link flow is separate from the chat or voice interaction. Any follow-up email to the user after form submission is out of scope unless explicitly implemented in a later phase.

## Error Handling Principles
The prototype should gracefully handle:
- unsupported topics
- unclear time preferences
- no slots available
- invalid booking codes
- booking update failures
- integration failures
- user attempts to share PII
- user requests for investment advice
- misunderstood or ambiguous user input
- correction turns such as "no, I meant tomorrow morning"
- repeat requests such as "can you say that again?"

## Non-Goals for Early Phases
The following should not be implemented in early phases (or remain out of scope for this prototype):
- **voice/STT/TTS inside `src/advisor_scheduler/`** (Phase 3 is client-side or a separate gateway; engine stays text-in/text-out)
- implementing the secure-details **backend form or storage** (the app returns a configured HTTPS URL with `code=`; a static demo page exists at `GET /secure-details` for UI experiments only)
- auto-sending emails
- production-grade persistence beyond append-only Sheets + in-process booking cache
- heavy eval framework

**Note:** Real Google Workspace MCP calls are **in scope for Phase 2** when configured (`USE_MCP=true` and valid MCP configs); they are optional for Phase 1-style runs that rely on stubs.

## Early-Phase Strategy
For cost control, early phases should rely on:
- deterministic **stub** adapters and mock slot grid when MCP is off
- transcript-based testing
- modular adapters
- isolated business logic
- minimal external API usage

With `USE_MCP=false`, the availability service uses deterministic mock availability. With `USE_MCP=true` and calendar MCP configured, availability is derived from Calendar free/busy (IST working hours from settings).

If the MCP free/busy read fails, the system must fail closed: it should not assume the calendar is empty and should not surface potentially booked slots as available. Instead, the assistant should tell the user that calendar availability could not be read right now and keep the interaction in a recoverable availability/day-selection state.

## Voice Interaction Best Practices
When voice support is added in a later phase, the assistant should follow these principles:
- keep spoken responses short and clear
- offer no more than two choices at a time
- confirm critical details such as topic, date, time, and timezone
- always repeat the selected slot before final confirmation
- support corrections and repeat requests gracefully
- clarify ambiguous date or time input before taking action
- avoid collecting any personal or sensitive information over voice
- use explicit transition language so the user understands each step of the flow
- keep fallback and error responses simple and easy to follow

## Closing Behavior
After completing any booking-related flow (new booking, reschedule, cancel, or waitlist):
- ask the user if there is anything else they need help with
- if the user has another request, transition into the new flow without re-greeting
- if the user has no further needs, end with a short closing message

## Acceptance Criteria
The prototype is successful if:
- all five required intents are supported
- the disclaimer is delivered early
- the assistant never collects PII
- the assistant never provides investment advice
- the assistant always uses IST for confirmed slots
- the assistant repeats the selected slot before confirmation
- the assistant asks for explicit final confirmation before triggering downstream actions
- explicit past dates are rejected consistently across deterministic and Gemini-assisted paths
- exact user picks of already offered slots are confirmed before any schedule-correction heuristics run
- MCP availability failures fail closed and never expose slots by assuming an empty busy list
- waitlist requires explicit user consent before creating any records
- booking code generation works and uniqueness is enforced
- successful booking triggers downstream adapter actions (stubs when `USE_MCP=false`, real MCP when `USE_MCP=true` and configured)
- booking-code lookup for later reschedule/cancel can be reconstructed from the latest append-only Sheets row for that code
- no-slot flow triggers waitlist behavior with user consent
- the assistant asks if anything else is needed after completing a flow
- the design cleanly supports future MCP and optional voice clients that send text to the same API
- the runtime engine prioritizes deterministic handling for typical flows so Gemini API usage stays low and quota is less likely to exhaust during normal booking tests and demos