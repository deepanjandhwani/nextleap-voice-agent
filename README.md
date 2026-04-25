# NextLeap Advisor Scheduler

Python chat-first advisor appointment scheduler: deterministic conversation engine, optional Gemini fallback, optional Google Workspace via in-repo FastMCP.

**Voice (Phase 3 MVP):** the web UI at `/` supports hold-to-talk: the browser records audio and the backend runs **Deepgram** STT/TTS on `POST /voice-turn`. Text chat can still use `POST /chat` with `channel: "voice"` so replies are shortened via `format_for_voice` for on-device read-aloud. Use HTTPS or localhost for microphone access; Chrome desktop is the most reliable target.

## Docs (aligned with code)

- **[docs/SOURCE_OF_TRUTH.md](docs/SOURCE_OF_TRUTH.md)** — which modules own which behavior (code wins when docs disagree).
- **[docs/README.md](docs/README.md)** — index of all product/operator docs.
- **[docs/architecture.md](docs/architecture.md)** — modules, data flow, HTTP API (includes deployment notes).

## Run locally

```bash
pip install -e ".[dev]"
cp .env.example .env   # configure GEMINI_API_KEY, DEEPGRAM_API_KEY, etc. as needed
python -m advisor_scheduler   # default http://127.0.0.1:8000 — UI at `/`, API at POST /chat
# If port 8000 is in use: set ADVISOR_API_PORT=8001 (or PORT=8001) and PUBLIC_BASE_URL=http://127.0.0.1:8001 in `.env`
```

## Deploy on Vercel

The repo is set up for **[Vercel’s FastAPI runtime](https://vercel.com/docs/frameworks/backend/fastapi)**:

- **Entrypoint:** [`api/index.py`](api/index.py) re-exports the app from `advisor_scheduler.api.app`.
- **Dependencies:** [`pyproject.toml`](pyproject.toml) (and [`requirements.txt`](requirements.txt) as `.[llm,mcp]` for installs that read it).
- **Static UI mirror:** [`public/index.html`](public/index.html) and [`public/secure-details/index.html`](public/secure-details/index.html) are copies of the files under `src/advisor_scheduler/api/static/`. Vercel can serve them from the edge; the FastAPI app still serves the same routes when those requests hit the function. **When you change the UI HTML, update both locations** (or run a copy step before deploy).

Connect the GitHub repo in the Vercel dashboard (or use `vercel link` / `vercel deploy`), then set **environment variables** to match production needs (see [`.env.example`](.env.example)). Important:

- **`PUBLIC_BASE_URL`** — set to your production origin (for example `https://your-project.vercel.app`, no trailing slash) so secure-link and CORS behavior match that host.
- **`GEMINI_API_KEY`**, **`DEEPGRAM_API_KEY`** — set if you use LLM fallbacks and server-side voice turns.
- **Google / MCP** — when `USE_MCP=true`, set Calendar/Sheets IDs plus Google OAuth JSON secrets. On serverless hosts, prefer `GOOGLE_OAUTH_TOKEN_JSON` and `GOOGLE_OAUTH_CREDENTIALS_JSON` over local file paths.

**Serverless caveats:** session state is **in-memory** in a single process. On Vercel, different requests may hit different instances or cold starts, so conversation continuity can differ from a single local `uvicorn` run until you add shared session storage. Long turns (especially `/voice-turn`) may need a higher **function max duration** in the Vercel project if you hit timeouts.

## Tests

```bash
python3 -m pytest tests/
```
