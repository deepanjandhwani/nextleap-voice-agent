# NextLeap Advisor Scheduler

Python chat-first advisor appointment scheduler: deterministic conversation engine, optional Gemini fallback, optional Google Workspace via in-repo FastMCP.

**Voice (Phase 3 MVP):** the web UI at `/` adds hold-to-talk (browser speech recognition) and read-aloud (`speechSynthesis`). Requests use `POST /chat` with optional `channel: "voice"` so replies are run through `format_for_voice` for clearer TTS. Use HTTPS or localhost for microphone access; Chrome desktop is the most reliable target.

## Docs (aligned with code)

- **[docs/SOURCE_OF_TRUTH.md](docs/SOURCE_OF_TRUTH.md)** — which modules own which behavior (code wins when docs disagree).
- **[docs/README.md](docs/README.md)** — index of all product/operator docs.

## Run locally

```bash
pip install -e ".[dev]"
cp .env.example .env   # configure GEMINI_API_KEY etc. as needed
python -m advisor_scheduler   # default http://127.0.0.1:8000 — UI at `/`, API at POST /chat
# If port 8000 is in use: set ADVISOR_API_PORT=8001 (or PORT=8001) and PUBLIC_BASE_URL=http://127.0.0.1:8001 in `.env`
```

## Tests

```bash
python3 -m pytest tests/
```
