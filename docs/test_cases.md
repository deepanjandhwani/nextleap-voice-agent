# Test Cases: Advisor Appointment Scheduler

## Purpose
This document defines the lightweight test scenarios for the Advisor Appointment Scheduler prototype.

The goal is not to build a heavy eval framework. The goal is to make sure the core logic, compliance behavior, and downstream orchestration work as expected.

**Integrations:** Expected downstream effects below describe **in-process stub** adapters (default for unit tests and `USE_MCP=false`). With `USE_MCP=true` and live Google MCP, the same flows trigger real Calendar, Sheets, and Gmail tools; compliance and confirmation rules are unchanged. **Behavior is defined by code** — see [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md).

## Testing Principles
- prioritize deterministic scenarios
- cover both happy paths and edge cases
- test compliance behavior explicitly
- test side effects only after explicit confirmation
- keep chat-first testing simple before voice is added
- **LLM usage:** core flows (typical book, reschedule, cancel, availability, waitlist) are exercised with **stub LLM** and should consume **zero** stub responses when the deterministic path handles every turn; remaining tests that still need structured JSON use the stub queue only when required

---

## 1. Happy Path: New Booking

### Scenario
The user wants to book a new appointment for a supported topic and selects one of the offered slots.

### Expected Outcome
- disclaimer is delivered early
- supported topic is captured
- preferred time is captured
- two slots are offered
- selected slot is repeated in IST
- explicit final confirmation is requested
- booking code is generated
- calendar hold stub is triggered
- Google Sheets append stub is triggered
- Gmail draft stub is triggered
- secure link is shared

### Regression: explicit calendar date + time window
The user gives a concrete calendar day and a coarse window (e.g. “afternoon on 25th April”) after choosing a topic. The engine must resolve the date and time window server-side and proceed to offer slots or waitlist—**not** loop with a generic request for “a specific day and time window” when the utterance is parseable.

### Regression: explicit past date rejection
If the user gives an explicit past day (especially with an explicit past year such as “15 April 2020”), the engine must reject it consistently and ask for a day from today onward in IST. It should not silently accept the date and should not depend on Gemini to catch this case.

### Regression: exact offered-slot choice wins over correction heuristics
When slots are already shown, a user reply such as “20 April 2026 2:00 PM”, “12:30”, or “second option” must confirm the matching offered slot before the engine treats the message as a broader day/time correction.

---

## 2. Happy Path: Reschedule

### Scenario
The user provides a valid booking code and reschedules to a new slot.

### Expected Outcome
- booking code is requested without asking for personal details
- alternative slots are offered
- selected slot is repeated in IST
- explicit final confirmation is requested
- downstream update stubs are triggered only after confirmation
- updated booking details are confirmed

---

## 3. Happy Path: Cancel

### Scenario
The user provides a valid booking code and cancels the booking.

### Expected Outcome
- booking code is requested without asking for personal details
- explicit cancellation confirmation is requested
- downstream cancellation updates are triggered only after confirmation
- cancellation is confirmed to the user

---

## 4. What-to-Prepare Intent

### Scenario
The user asks what to prepare for a supported topic.

### Expected Outcome
- disclaimer is delivered early
- generic preparation guidance is provided
- no personal data is requested
- no investment advice is given
- the assistant may optionally offer booking support

---

## 5. Check Availability Intent

### Scenario
The user asks for available windows before deciding to book.

### Expected Outcome
- disclaimer is delivered early
- availability windows are given in IST
- the assistant may transition into booking flow if the user wants to proceed
- no downstream side effects occur unless the user confirms a booking
- if the requested day is in the past, the assistant asks for a day from today onward in IST
- if MCP free/busy cannot be fetched, the assistant does not expose speculative availability and instead asks the user to retry later or provide another day

---

## 6. No Slots Available / Waitlist

### Scenario
The availability service returns no suitable slots.

### Expected Outcome
- the user is informed clearly that no matching slots are available
- the assistant offers waitlist handling and waits for explicit user consent
- on consent, booking code is generated
- Google Sheets waitlist row append stub is triggered with status `waitlisted`
- waitlist Gmail draft stub is triggered
- secure link is shared

---

## 7. Unsupported Topic

### Scenario
The user asks for help outside the supported topics.

### Expected Outcome
- the assistant does not proceed with unsupported topic handling as if it were valid
- the assistant redirects the user to the allowed topic list
- no booking side effects occur until a supported topic is selected

---

## 8. Invalid Booking Code for Reschedule

### Scenario
The user tries to reschedule using an invalid booking code.

### Expected Outcome
- the assistant says the booking code could not be found
- the assistant asks the user to retry the booking code
- the assistant does not ask for personal details
- no downstream updates occur
- after repeated failures, the flow closes gracefully with a retry-or-new-booking message

---

## 9. Invalid Booking Code for Cancel

### Scenario
The user tries to cancel using an invalid booking code.

### Expected Outcome
- the assistant says the booking code could not be found
- the assistant asks the user to retry the booking code
- no downstream updates occur
- after repeated failures, the flow closes gracefully with a retry message and no cancellation side effect

---

## 10. PII Interruption: Phone Number

### Scenario
The user tries to share a phone number.

### Expected Outcome
- the assistant interrupts politely
- the assistant tells the user not to share personal details here
- the assistant redirects to topic and scheduling details only
- the interaction continues without storing the phone number

---

## 11. PII Interruption: Email Address

### Scenario
The user tries to share an email address.

### Expected Outcome
- the assistant interrupts politely
- the assistant tells the user not to share personal details here
- the assistant reminds the user that contact details can be submitted through the secure link later

---

## 12. PII Interruption: Account Identifier

### Scenario
The user tries to share an account number, PAN, Aadhaar, or similar identifier.

### Expected Outcome
- the assistant interrupts politely
- the assistant refuses collection of that information in the interaction
- the assistant redirects to non-sensitive flow details only

---

## 13. Investment Advice Refusal

### Scenario
The user asks for personalized investment advice.

### Expected Outcome
- the assistant refuses politely
- the assistant reminds the user that this interaction is for informational support only
- the assistant offers educational help or booking support instead
- no investment recommendation is given

---

## 14. Confirmation Requirement Before Side Effects

### Scenario
The user selects a slot but does not confirm it yet.

### Expected Outcome
- the assistant repeats the slot in IST
- the assistant asks for explicit final confirmation
- no booking code is finalized and no downstream stubs are triggered before confirmation

---

## 15. IST Mention Requirement

### Scenario
The assistant offers slots or confirms a selected slot.

### Expected Outcome
- timezone is stated as IST in booking-related confirmations
- slot repetition before confirmation includes IST

---

## 16. Correction Turn: Topic

### Scenario
The user changes the topic after initially choosing one.

### Expected Outcome
- the assistant acknowledges the correction
- the state updates to the new topic
- the flow continues without restarting the entire interaction
- if the day/time is already known from a prior turn, the flow can move straight back to slot offering

---

## 17. Correction Turn: Time Preference

### Scenario
The user changes the preferred day or time after initially stating it.

### Expected Outcome
- the assistant acknowledges the correction
- the state updates to the corrected preference
- fresh slot options are fetched if needed
- the flow continues cleanly
- if the user corrects the choice during confirmation, the pending slot changes without restarting the full flow

---

## 18. Repeat Request

### Scenario
The user asks the assistant to repeat the slot options or confirmation details.

### Expected Outcome
- the assistant repeats only the relevant information
- repeated slot options remain short and clear
- repeated slot options are still communicated in IST
- if the user repeats during confirmation, only the pending slot and confirmation ask are replayed

---

## 19. Ambiguous Time Input

### Scenario
The user says something vague like "tomorrow morning" or "later in the week."

### Expected Outcome
- the assistant asks a clarification question
- the assistant does not trigger downstream actions until enough detail is gathered

---

## 20. Integration Failure Handling

### Scenario
One of the downstream stub calls returns failure.

### Expected Outcome
- the failure is handled gracefully
- the assistant does not expose internal technical details
- the orchestration layer records failure appropriately
- the user-facing message remains clear and simple

---

## 21. Booking Code Format

### Scenario
A booking-related flow generates a booking code.

### Expected Outcome
- booking code matches format `NL-XXXX`

---

## 22. Waitlist Status Recording

### Scenario
The user is placed on the waitlist.

### Expected Outcome
- Google Sheets row status is `waitlisted`
- waitlist draft content reflects no-slot condition
- booking code is still generated and shared
- secure link is shared only when the configured base URL is valid

---

## 23. Reschedule Status Recording

### Scenario
A booking is successfully rescheduled.

### Expected Outcome
- Google Sheets row or update reflects `rescheduled`
- previous slot can be retained if implemented
- updated slot is reflected in the downstream records

---

## 24. Cancel Status Recording

### Scenario
A booking is successfully cancelled.

### Expected Outcome
- Google Sheets row or update reflects `cancelled`
- calendar hold update is attempted if supported
- cancellation confirmation is given to the user

---

## 25. User Declines Waitlist

### Scenario
The availability service returns no suitable slots and the user declines the waitlist offer.

### Expected Outcome
- the assistant offers waitlist handling
- the user declines
- no booking code is generated
- no Google Sheets row is appended
- no Gmail draft is created
- the assistant offers to search for a different day or time
- if the user does not want to try again, the session ends gracefully

---

## 26. Check Availability Without Topic

### Scenario
The user asks for available slots without specifying a consultation topic.

### Expected Outcome
- the assistant does not require a topic for availability checks
- availability windows are returned in IST
- the assistant may transition into booking flow if the user wants to proceed
- topic is collected only if the user decides to proceed with booking

---

## 27. MCP Availability Failure Fails Closed

### Scenario
The system is using Google Calendar free/busy via MCP and the free/busy lookup fails.

### Expected Outcome
- the assistant does not assume the calendar is empty
- no speculative slots or availability windows are shown
- the assistant says calendar availability could not be read right now
- the interaction stays in a recoverable day-selection state so the user can retry

---

## 28. Past-Date Rejection Across Paths

### Scenario
The user gives a day earlier than today in IST, either through deterministic parsing or through a phrase that would otherwise go through Gemini resolution.

### Expected Outcome
- the assistant rejects the day consistently
- the assistant asks for a day from today onward in IST
- no slot lookup or downstream action occurs for the past day
- deterministic explicit past dates do not require Gemini to be called

---

## 29. Offered Slot Selection by Exact Date/Time

### Scenario
The user is looking at offered slots and replies with a full date+time phrase that exactly matches one of them.

### Expected Outcome
- the assistant maps the utterance to the matching offered slot
- the assistant moves to confirmation for that slot
- the assistant does not re-offer slots as if the user changed the broader schedule preference

---

## 30. Mid-Session Intent Switch With Partial State

### Scenario
The user is mid-booking (e.g. topic and time collected), switches to cancel, then switches back to booking.

### Expected Outcome
- the assistant handles each intent switch with a short transition
- the assistant does not greet again
- session-level context (greeting delivered, disclaimer delivered) is preserved
- flow-specific state may be carried over or cleanly reset depending on the new intent
- no state corruption occurs across switches

---

## 31. Duplicate Booking Code Prevention

### Scenario
Multiple bookings are created in the same session or across sessions.

### Expected Outcome
- every generated booking code is unique
- no two bookings share the same code, even in mock mode
- the booking service enforces uniqueness before returning a code

---

## 32. Integration Partial Failure

### Scenario
One downstream stub call succeeds (e.g. calendar hold) but another fails (e.g. Google Sheets append).

### Expected Outcome
- the failure is handled gracefully
- the assistant does not expose internal technical details to the user
- the user-facing message remains clear and indicates that something went wrong
- the orchestration layer records the partial failure appropriately
- the booking status reflects `failed` if critical side effects could not complete

---

## 33. Session Timeout Behavior

### Scenario
A user begins a booking flow but stops responding. After 20 minutes of inactivity, the session times out. The user then sends a new message.

### Expected Outcome
- the timed-out session state is cleared
- the new message starts a fresh session with a new greeting and disclaimer
- any booking records created before the timeout remain intact in the booking store
- the user is not penalized or confused by the timeout

---

## Suggested Minimum Automated Coverage
At minimum, automate these scenarios first:
1. happy path booking
2. no-slot waitlist with consent
3. user declines waitlist
4. PII interruption
5. investment-advice refusal
6. invalid booking code
7. confirmation-before-side-effects
8. IST confirmation behavior
9. past-date rejection
10. exact offered-slot selection
11. MCP availability fail-closed behavior
12. session timeout behavior

## Suggested Test Types
Use a mix of:
- unit tests for guards and services
- transcript-style tests for conversation behavior
- orchestration tests with stub adapters