# MCP Contracts: Advisor Appointment Scheduler

**Source of truth:** adapter protocols and tool implementations in [`src/advisor_scheduler/integrations/`](../src/advisor_scheduler/integrations/); this document mirrors them.

## Purpose
This document defines the MCP-facing contracts for the downstream actions used by the Advisor Appointment Scheduler.

For this project, MCP integrations are used for:
- Google Calendar tentative hold creation
- Google Sheets booking record append
- Gmail draft creation

These contracts should remain stable even if the internal implementation changes.

## General Principles
- all downstream actions should be triggered only after explicit confirmation from the user
- all external systems should be accessed through adapters
- with `USE_MCP=false`, in-process **stubs** conform to the same request/response shapes as live MCP
- all times should be stored and communicated with timezone clarity
- the booking code should be included in all downstream records where relevant

**Runtime modes:** With `USE_MCP=false`, adapters are in-process stubs. With `USE_MCP=true`, the same protocol implementations call a single in-repo Python FastMCP server (see [`src/advisor_scheduler/integrations/google_workspace/server.py`](../src/advisor_scheduler/integrations/google_workspace/server.py)). The server exposes a narrow tool contract:

- `calendar_create_hold`
- `calendar_update_hold` (reschedule: patch existing event when `booking.calendar_hold_id` is known)
- `calendar_delete_hold` (cancel: remove the calendar event when a hold id exists)
- `calendar_get_freebusy`
- `sheets_append_prebooking`
- `sheets_list_prebookings`
- `gmail_create_draft`

OAuth lives in the in-repo server (one combined consent across Calendar, Sheets, and Gmail; refresh token cached locally). Tool names are configurable via `MCP_TOOL_*` settings only when pointing the adapters at a custom server with different labels.

---

## 1. Google Calendar Hold Contract

### Purpose
Create or update a tentative advisor hold.

### Action Type
Calendar hold create or update

### Suggested Title Format
`Advisor Q&A — {Topic} — {Code}`

Example:
`Advisor Q&A — KYC / Onboarding — NL-A742`

### Request Shape
- title
- start_time
- end_time
- timezone
- status
- metadata

### Field Notes
- `title`: formatted hold title
- `start_time`: selected slot start
- `end_time`: selected slot end (all slots are 30 minutes; `end_time` = `start_time` + 30 min)
- `timezone`: should be `IST` or the underlying canonical timezone representation used internally
- `status`: expected values can include `tentative`, `rescheduled`, `cancelled`, `waitlisted`
- `metadata`: structured fields for traceability

### Suggested Metadata Fields (optional for Phase 1 stubs)
- booking_code
- topic
- booking_type
- source
- user_intent

### Example Request
- title: `Advisor Q&A — Statements / Tax Docs — NL-A742`
- start_time: `2026-04-20T11:00:00`
- end_time: `2026-04-20T11:30:00`
- timezone: `Asia/Kolkata`
- status: `tentative`
- metadata:
  - booking_code: `NL-A742`
  - topic: `Statements / Tax Docs`
  - booking_type: `new_booking`
  - source: `advisor_scheduler`
  - user_intent: `book_new`

### Response Shape
- success
- external_id
- status
- message

### Response Notes
- `success`: boolean
- `external_id`: provider-specific identifier if available
- `status`: normalized internal status
- `message`: optional diagnostic message

---

## 2. Google Sheets Append Contract

### Purpose
Append a booking-related row to the pre-bookings sheet.

### Target Sheet
Spreadsheet/tab:
`Advisor Pre-Bookings`

This sheet serves as the structured pre-bookings store for the prototype.

### Action Type
Row append only (event log model).

In Phase 1, the sheet is treated as an append-only event log. Every booking action (new, reschedule, cancel, waitlist) appends a new row. The current state of a booking is determined by the latest row for a given `booking_code`. Rows are never mutated in place.

**Implementation note:** The in-repo `sheets_append_prebooking` tool measures the next empty row using column `A`, then writes each payload row to an explicit `A{row}:P{row}` range (16 columns). This avoids layout drift from broad `A:Z` append behavior. The companion `sheets_list_prebookings` tool reads the same schema back so runtime booking lookup can reconstruct the latest state per `booking_code`. Client adapters must send rows with values in the column order below.

### Column order (A through P)
All sixteen columns are present in each appended row (empty string when not applicable):

1. created_at  
2. updated_at  
3. booking_code  
4. topic  
5. intent  
6. requested_day  
7. requested_time_window  
8. confirmed_slot  
9. timezone  
10. status  
11. source  
12. notes  
13. calendar_hold_id  
14. email_draft_id  
15. previous_slot  
16. action_type  

### Field Notes
- `created_at`: timestamp for initial creation
- `updated_at`: timestamp for latest change
- `booking_code`: generated booking code
- `topic`: selected supported topic
- `intent`: `book_new`, `reschedule`, `cancel`, `check_availability`, etc. where relevant
- `requested_day`: user’s preferred day if captured
- `requested_time_window`: morning/afternoon/evening or equivalent
- `confirmed_slot`: final selected slot in a normalized format
- `timezone`: `IST` or canonical internal timezone representation
- `status`: `tentative`, `waitlisted`, `rescheduled`, `cancelled`
- `source`: `advisor_scheduler`

### Example Row
- created_at: `2026-04-15T10:00:00+05:30`
- updated_at: `2026-04-15T10:00:00+05:30`
- booking_code: `NL-A742`
- topic: `Withdrawals / Timelines`
- intent: `book_new`
- requested_day: `Tuesday`
- requested_time_window: `afternoon`
- confirmed_slot: `Tuesday, 21 Apr 2026 at 15:00 IST` (human-readable slot label from the scheduler)
- timezone: `Asia/Kolkata`
- status: `tentative`
- source: `advisor_scheduler`
- notes: *(empty for a normal booking)*
- calendar_hold_id: *(event id when Calendar write succeeds)*
- email_draft_id: *(draft id when Gmail draft succeeds)*
- previous_slot: *(empty on first booking)*
- action_type: `new_booking`

### Response Shape
- success
- row_identifier
- status
- message

---

## 3. Gmail Draft Contract

### Purpose
Prepare an advisor-facing email draft after booking confirmation or waitlist handling.

### Action Type
Draft create or draft update

### Approval Rule
Email draft creation is in scope.
Automatic sending should not be assumed unless explicitly implemented later.

### Request Shape
- to
- subject
- body
- approval_required
- metadata

### Field Notes
- `to`: internal recipient address from environment configuration (e.g. `ADVISOR_EMAIL` env var). This is never collected from the user.
- `subject`: standardized subject including topic and booking code
- `body`: concise operational summary
- `approval_required`: should be `true`
- `metadata`: structured traceability fields (optional for Phase 1 stubs)

### Phase 1 Gmail Rules
- drafts are internal to the configured mailbox only
- no emails are auto-sent in Phase 1
- the sender identity and mailbox come from environment/config, never from user input
- the assistant must never ask the user for an email address

### Suggested Subject Format
`Tentative Advisor Booking — {Topic} — {Code}`

Example:
`Tentative Advisor Booking — SIP / Mandates — NL-A742`

### Body Should Include
- booking code
- selected topic
- selected slot or waitlist status
- booking status
- source
- note that user contact details will be submitted separately through the secure link

### Suggested Metadata Fields (optional for Phase 1 stubs)
- booking_code
- topic
- booking_status
- source
- user_intent

### Example Request
- to: `advisor-team@example.com`
- subject: `Tentative Advisor Booking — KYC / Onboarding — NL-A742`
- body:
  - Booking code: NL-A742
  - Topic: KYC / Onboarding
  - Slot: 2026-04-21 11:00 IST
  - Status: tentative
  - Source: advisor_scheduler
  - Contact details will be collected separately through the secure link.
- approval_required: `true`
- metadata:
  - booking_code: `NL-A742`
  - topic: `KYC / Onboarding`
  - booking_status: `tentative`
  - source: `advisor_scheduler`
  - user_intent: `book_new`

### Response Shape
- success
- draft_id
- status
- message

---

## 4. Waitlist Contract Notes

### Purpose
Handle no-slot cases consistently.

### Waitlist Behavior
When no suitable slots are found:
- generate booking code
- append waitlist row to Google Sheets
- prepare waitlist email draft
- optionally create a placeholder calendar record if your implementation supports that

### Waitlist Sheet Status
Use:
- `waitlisted`

### Waitlist Draft Subject Example
`Waitlist Request — {Topic} — {Code}`

### Waitlist Draft Body Should Include
- booking code
- topic
- note that no suitable slot was available
- source
- note that user contact details will be submitted separately through the secure link

---

## 5. Reschedule Contract Notes

### On Successful Reschedule
Downstream actions:
- update calendar hold
- append a new Google Sheets row with status `rescheduled` (do not mutate existing rows)
- update or create Gmail draft

Booking-code lookup for later reschedule/cancel turns should use the latest persisted row for that code from the append-only sheet when available.

### Additional Suggested Sheet Fields for Reschedule
- previous_slot
- updated_at
- action_type = `reschedule`

---

## 6. Cancel Contract Notes

### On Successful Cancellation
Downstream actions:
- append a new Google Sheets row with status `cancelled` (do not mutate existing rows)
- update calendar hold if supported
- update or create draft if needed

### Additional Suggested Sheet Fields for Cancel
- updated_at
- action_type = `cancel`

---

## 7. Normalized Status Values
Use these normalized statuses wherever possible:
- tentative
- waitlisted
- rescheduled
- cancelled
- failed

---

## 8. Source Value
Use a consistent source value for traceability:
- `advisor_scheduler`

---

## 9. Early-Phase Stub Guidance
In early phases:
- use stub adapters that accept the same request shapes
- return normalized response objects
- avoid real external side effects until the flow is stable
- metadata fields are optional for Phase 1 stubs; populate them when convenient but do not block on them

This will keep the implementation easy to test and cheap to iterate on.