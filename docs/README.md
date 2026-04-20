# Documentation index

**Authoritative behavior:** [`src/advisor_scheduler/`](../src/advisor_scheduler/) (see **[SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md)**). These docs describe that code; when they disagree, update the doc.

| Document | Purpose |
|----------|---------|
| **[SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md)** | Code map: modules, routes, states, phases |
| [architecture.md](architecture.md) | Modules, data flow, hybrid engine, state machine, HTTP |
| [advisor_scheduler_spec.md](advisor_scheduler_spec.md) | Product spec: intents, compliance, booking, MCP |
| [conversation_flows.md](conversation_flows.md) | Interaction-level flows |
| [mcp_contracts.md](mcp_contracts.md) | Adapter / MCP tool contracts |
| [phase2_operator_setup.md](phase2_operator_setup.md) | Operator checklist: live Google |
| [GOOGLE_MCP_QUICKSTART.md](GOOGLE_MCP_QUICKSTART.md) | OAuth + FastMCP client setup (`scripts/setup_google_mcp.py`) |
| [GOOGLE_MCP_SUMMARY.md](GOOGLE_MCP_SUMMARY.md) | Short summary of the in-repo Google Workspace server |
| [google_mcp_setup.md](google_mcp_setup.md) | Full Google + FastMCP setup (same stack as Quickstart; cross-link) |
| [test_cases.md](test_cases.md) | Test scenarios aligned with `tests/` |

**Verify MCP tools:** `python -m advisor_scheduler.cli.mcp_list_tools`

---

## What is still pending? (plain language)

**Phase 1 (chat product)** — The engine, `/chat`, stubs, and tests are **implemented**. What may still be open is **your own bar for “done”**: extra edge-case tests, copy tweaks, or backlog items from internal plans—not “missing Phase 1.”

**Phase 2 (live Google)** — The **code path** exists (`use_mcp=true`, in-repo server). What is often still **pending** is **operator work**:

- Example: you have not yet put real OAuth tokens and spreadsheet IDs in `.env`, so bookings still use **stubs**.  
- Example: you have not run `MCP_LIVE_TEST=1 pytest tests/test_mcp_adapters.py -m mcp` successfully against real Google.

See [phase2_operator_setup.md](phase2_operator_setup.md) and [`.cursor/plans/Phase_2_pending.md`](../.cursor/plans/Phase_2_pending.md).

**Phase 3 (voice)** — **MVP in the static UI:** the chat page uses the **Web Speech API** (hold mic → STT → `POST /chat` with `channel: "voice"` → TTS). Server-side **`formatters/voice.py`** shortens the assistant reply for listening. Cloud STT/TTS and streaming audio are **not** in this repo.
