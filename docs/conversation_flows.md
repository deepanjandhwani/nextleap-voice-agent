# Conversation Flows: Advisor Appointment Scheduler

**Implementation reference:** [`src/advisor_scheduler/core/engine.py`](../src/advisor_scheduler/core/engine.py) and [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md).

## Purpose
This document defines the expected conversation flows for all supported intents in the Advisor Appointment Scheduler.

These flows are written at the product and interaction level. They are not tied to any specific UI; the live behavior is defined by the Python engine above.

**Implementation note:** The production engine resolves most steps **without calling an LLM** when user input matches heuristics (intent hints, allowed topics, parsable dates/times, slot picks, booking codes, yes/no). Gemini is used as a **fallback** for ambiguous turns, to save API quota and keep responses compliant. Product-facing wording should still match these flows.

## Global Rules for All Flows
The following rules apply across all flows:
- deliver the disclaimer early:
  "This conversation is for informational support only and does not provide investment advice."
- greet only once at the start of a session
- if the user switches intent mid-conversation, do not greet again; use a short transition instead
- repeat the disclaimer only when needed, such as at the start of a new session or when the conversation returns to advisory-sensitive territory
- do not collect PII in the interaction
- do not ask for phone number, email address, account number, PAN, Aadhaar, or similar identifiers
- if the user shares PII, interrupt politely and redirect them
- do not provide investment advice
- use IST whenever availability or confirmations are discussed
- repeat the selected slot before final confirmation
- require explicit final confirmation before any booking, reschedule, or cancellation side effects
- if the secure details link is not configured correctly, do not share a broken URL; tell the user the link is unavailable right now
- keep responses short and clear
- offer no more than two slot options at a time
- after completing any booking-related flow, ask the user if there is anything else they need help with; if not, end with a short closing message

---

## 1. New Booking Flow

### Goal
Help the user book a tentative advisor slot.

### Flow
1. greet the user only if this is the start of the session
2. state the disclaimer
3. identify that the user wants to book an appointment
4. ask for or confirm the consultation topic from the allowed list
5. collect preferred day and time window
6. fetch up to two matching slots from the availability service
7. present the two slot options in IST
8. ask the user to choose one
9. repeat the chosen slot in IST
10. ask for final confirmation
11. on confirmation:
   - generate booking code
   - create tentative calendar hold
   - append booking row to Google Sheets tab `Advisor Pre-Bookings`
   - prepare advisor email draft
12. read back the booking code
13. share secure link for contact-detail submission outside the interaction
14. ask the user if there is anything else they need help with
15. if not, end with a short closing message

### Example prompt sequence
- "What would you like help with today?"
- "Please choose one of these topics: KYC and onboarding, SIP and mandates, statements and tax docs, withdrawals and timelines, or account changes and nominee."
- "What day and time would you prefer?"
- "I found two options in IST: Tuesday at 3 PM, or Wednesday at 11 AM. Which works better for you?"
- "Let me confirm: Wednesday at 11 AM IST. Should I go ahead and place the tentative booking?"

Users may phrase timing in natural language; examples that should be accepted without an extra “which day?” loop when the backend can parse them include:
- "Monday morning"
- "afternoon on 25th April"
- "April 25 afternoon"

Availability and confirmations are always framed in IST.

If the user provides a day that resolves to a past calendar date in IST, the assistant should not proceed to slot lookup. It should clearly ask for a day from today onward in IST.

If the user changes the requested day, time window, or topic mid-flow, the engine should update that field and continue without restarting the booking flow.

---

## 2. Reschedule Flow

### Goal
Allow the user to reschedule an existing tentative booking using only the booking code.

### Flow
1. greet the user only if this is the start of the session
2. state the disclaimer if not already delivered in the session
3. identify reschedule intent
4. ask for booking code only
5. validate booking code
6. if valid, ask for new preferred day and time window
7. fetch up to two alternative slots
8. present alternatives in IST
9. ask the user to choose one
10. repeat the selected slot in IST
11. ask for final confirmation
12. on confirmation:
   - update booking record
   - update calendar hold
   - append a new row to the append-only Google Sheets event log
   - update or prepare draft as needed
13. confirm that the reschedule is complete

### If booking code is invalid
- say the code could not be found
- ask the user to retry the booking code
- do not ask for personal details
- allow up to 3 retry attempts
- after 3 failed attempts, suggest double-checking the code or starting a new booking

### If the user rejects the offered reschedule slot
- do not reschedule anything
- clear the pending slot
- re-show the available reschedule options in IST
- let the user pick another option or correct the date/time

If the user replies with an exact offered slot reference such as a concrete date+time (`20 April 2026 2:00 PM`), a bare clock time (`12:30`), or an ordinal choice (`second option`), the assistant should treat that as a slot selection first, not as a correction to the broader schedule request.

### Example transition if user switches from booking to reschedule mid-session
- "Sure, I can help you reschedule instead. Please share your booking code."

---

## 3. Cancel Flow

### Goal
Allow the user to cancel an existing tentative booking using only the booking code.

### Flow
1. greet the user only if this is the start of the session
2. state the disclaimer if not already delivered in the session
3. identify cancellation intent
4. ask for booking code only
5. validate booking code
6. ask for explicit cancellation confirmation
7. on confirmation:
   - update booking record as cancelled
   - update calendar hold if needed
   - update or prepare cancellation draft if needed
8. confirm cancellation to the user

### If booking code is invalid
- say the code could not be found
- ask the user to retry the booking code
- do not ask for personal details
- allow up to 3 retry attempts
- after 3 failed attempts, suggest double-checking the code or starting a new booking

Booking lookup should use the latest append-only Google Sheets row for the booking code when available, with in-memory state treated as a cache rather than the only source of truth.

### Example transition if user switches from another flow
- "Okay, I can help cancel that. Please share your booking code."

In slot-selection or confirmation states, phrases like "cancel that" should be treated as a local rejection of the current option, not as an automatic cancel-booking intent.

---

## 4. What-to-Prepare Flow

### Goal
Help the user understand what to prepare before a consultation.

### Flow
1. greet the user only if this is the start of the session
2. state the disclaimer if not already delivered in the session
3. identify what-to-prepare intent
4. ask which supported topic they need preparation guidance for if not already known
5. provide generic preparation guidance relevant to the selected topic
6. do not ask for personal or account-specific details
7. optionally ask whether the user would also like to book a slot

### Guidance style
- high level
- topic-specific
- non-advisory
- no account review
- no personalized recommendations

### Example transition if user switches from another flow
- "Sure, I can help with that first. Which topic do you want to prepare for?"

---

## 5. Check Availability Intent Flow

### Goal
Let the user explore availability before booking.

### Flow
1. greet the user only if this is the start of the session
2. state the disclaimer if not already delivered in the session
3. identify availability-check intent
4. ask for preferred day if needed (topic is not required for availability checks in Phase 1)
5. fetch availability windows
6. read out available windows in IST
7. ask whether the user wants to proceed to booking

### Example
- "I currently see availability in IST on Tuesday afternoon and Wednesday morning. Would you like me to help you book one of those?"

### Example transition if user switches from another flow
- "Sure, let me check availability first."

### If the requested day is in the past
- do not read out availability for that day
- ask the user for a day from today onward in IST

### If MCP calendar availability cannot be read
- do not assume the calendar is free
- do not offer slots from a failed free/busy lookup
- tell the user that calendar availability could not be read right now
- keep the user in a recoverable availability/day-selection state so they can retry with another day or try again shortly

---

## 6. Waitlist Flow

### Goal
Handle cases where no matching slots are available.

### Trigger
This flow is triggered when the availability service returns no suitable slots.

### Flow
1. inform the user that no matching slots are currently available
2. offer waitlist handling and wait for explicit user consent
3. if the user consents:
   - generate booking code
   - create waitlist record (append to Google Sheets with status `waitlisted`)
   - prepare waitlist advisor email draft
   - share secure link for contact-detail submission
   - confirm the booking code and waitlist status
   - ask the user if there is anything else they need help with
   - if not, end with a short closing message

### If the user declines the waitlist
- do not create any records
- do not generate a booking code
- offer to search for a different day or time window
- if the user does not want to try again, end with a short closing message

### Example
- "I couldn't find a matching slot right now. I can place you on the waitlist instead. Would you like me to do that?"
- If declined: "No problem. Would you like to try a different day or time?"

---

## 7. Investment Advice Refusal Flow

### Goal
Refuse investment advice requests while staying helpful.

### Trigger
The user asks for:
- specific investment recommendations
- whether they should buy, sell, redeem, switch, or hold
- personalized financial advice

### Flow
1. politely refuse
2. remind the user that this interaction is for informational support only
3. redirect to educational help or appointment booking
4. continue only if the user returns to an allowed topic

### Example
- "I'm not able to provide investment advice here. I can help with informational support or help you book an advisor appointment."

---

## 8. PII Interruption Flow

### Goal
Prevent collection of sensitive personal information.

### Trigger
The user starts sharing:
- phone number
- email address
- account number
- PAN
- Aadhaar
- any other personal identifier

### Flow
1. interrupt politely
2. ask the user not to share personal details in the interaction
3. redirect them to topic or scheduling details only
4. if needed, remind them that contact details can be submitted through the secure link later

### Example
- "Please don't share personal or account details here. I can continue helping with the topic and preferred time, and you can submit contact details later through the secure link."

---

## 9. Correction Flow

### Goal
Allow the user to revise an earlier answer without restarting.

### Common examples
- "No, I meant next Friday."
- "Actually, I want help with tax docs."
- "Not afternoon, morning."

### Flow
1. acknowledge the correction
2. update the relevant state
3. continue from the corrected step
4. avoid restarting the entire interaction unless necessary

### Principle
Corrections should be lightweight and should not force the user to repeat everything.

The most common lightweight corrections are:
- changing topic while still in booking/preparation flow
- changing the requested day or time window after slots were shown
- changing the chosen slot while already in confirmation

When both interpretations are possible, the engine should prefer an exact match to one of the already offered slots before treating the message as a correction to the broader requested schedule.

---

## 10. Repeat Request Flow

### Goal
Support users who ask the assistant to repeat information.

### Trigger
Examples:
- "Can you repeat that?"
- "Say the options again."
- "What was the second slot?"

### Flow
1. repeat only the necessary information
2. keep the repeated response short
3. if repeating slot options, repeat in IST
4. return to the pending question

Examples of the "necessary information" rule:
- for slot offers, repeat only the current one or two options and the pending question
- for confirmation, repeat only the selected slot and the yes/no confirmation ask
- for availability, repeat only the last availability summary

---

## 11. Ambiguity Clarification Flow

### Goal
Clarify unclear or incomplete inputs before side effects occur.

### Common ambiguous inputs
- "tomorrow"
- "later"
- "morning"
- "next week"
- "whenever available"

### Flow
1. identify that the input is too vague
2. ask a narrower clarification question
3. continue only after enough detail is available

### Example
- "When you say tomorrow, would you prefer morning or afternoon in IST?"

### Non-ambiguous but invalid date input
Inputs such as an explicitly past year or a calendar day earlier than today in IST should not be treated as normal ambiguity. The assistant should reject the day and ask for a date from today onward in IST.

---

## 12. Mid-Conversation Intent Switch Flow

### Goal
Handle users who change what they want in the middle of the session.

### Examples
- "Actually, can I reschedule instead?"
- "Wait, first tell me what I should prepare."
- "No, just cancel it."

### Flow
1. acknowledge the change in intent
2. do not greet again
3. use a short transition statement
4. preserve relevant session context where appropriate
5. move into the newly selected flow

### Example transitions
- "Sure, I can help you reschedule instead."
- "Okay, let's switch to cancellation."
- "Got it. Let me help with preparation first."

---

## 13. Closing Flow

### Goal
End the session gracefully after completing a flow.

### Trigger
After any completed flow (booking, reschedule, cancel, waitlist, preparation guidance, or availability check).

### Flow
1. ask the user if there is anything else they need help with
2. if the user has another supported request, transition directly into the new flow without re-greeting or an extra "what else can I help with?" turn
3. if the user has no further needs, end with a short closing message

### Example
- "Is there anything else I can help you with?"
- If done: "You're all set. Have a great day!"

---

## Allowed Topics Reference
The only supported topics are:
- KYC / Onboarding
- SIP / Mandates
- Statements / Tax Docs
- Withdrawals / Timelines
- Account Changes / Nominee

If the user goes outside these topics, redirect them back to the supported list.

---

## Final Confirmation Rule
Before any side effect is triggered, the assistant must:
- repeat the selected date and time in IST
- ask for explicit final confirmation
- proceed only after confirmation is received
