# Phase 1: Bootstrap & Session - Context

**Gathered:** 2026-05-12
**Status:** Ready for planning
**Mode:** Auto-generated (smart discuss — decisions pulled from research)

<domain>
## Phase Boundary

Project scaffold, Python packaging, typed configuration loading from env, Telethon client wrapper that loads a persisted session, and a one-time interactive login script the user runs manually from their local machine. No event handlers, no audio, no whisper — just "can we log into Telegram and print `connected as @me`".

Scope excludes: any message handler, any audio processing, any transcription, any systemd or VPS work. Those belong to later phases.

</domain>

<decisions>
## Implementation Decisions

### Project Layout
- Package name: `tg_voice_transcriber` (Python import) / `telegram-voice-transcriber` (distribution)
- Source root: `src/tg_voice_transcriber/` (src-layout)
- Script entry points: `python -m tg_voice_transcriber` (main, later phases) and `python scripts/login.py` (interactive one-time login)
- Python version pin: `requires-python = ">=3.11,<3.13"` — 3.11 is the sweet spot; avoid 3.13 because some ML wheels lag

### Dependencies
- Telethon `>=1.43,<2.0` — pin strictly below 2.0 (alpha, breaking changes)
- pydantic `>=2.6,<3.0`
- pydantic-settings `>=2.2,<3.0`
- python-dotenv `>=1.0,<2.0` for local-dev `.env` convenience
- structlog `>=24.1` for structured JSON-ish logging
- Dev: ruff, pytest, pytest-asyncio (declared as optional `[project.optional-dependencies].dev`)

### Configuration
- Single `Config` class built on `pydantic_settings.BaseSettings`
- Env-prefix: `TG_VOICE_` (e.g. `TG_VOICE_API_ID`, `TG_VOICE_API_HASH`, `TG_VOICE_PHONE`, `TG_VOICE_SESSION_PATH`)
- Loads from process env; in dev, `python-dotenv` hooks `.env` if present
- Secrets never logged. `.env` gitignored from day 1.
- Session path default: `./.local/userbot.session` for dev; production systemd uses `/var/lib/tg-voice-transcriber/userbot.session` via env override.

### Telethon Client Wrapper
- `TelegramUserbot` class encapsulates `TelegramClient` construction from Config
- Exposes `start()` (connect + session load), `stop()` (disconnect cleanly), `is_authorized()` check
- On `AuthKeyError` / `UserDeactivatedError`, raises a typed `SessionInvalidError` so `main()` can exit non-zero with AUTH_REQUIRED log line
- No event handlers yet — that's Phase 4
- Uses `StringSession` → `SQLiteSession` file path. Default is SQLite session file (Telethon native)

### Interactive Login Script
- `scripts/login.py` — standalone script, NOT the main entry
- Runs `client.start(phone=cfg.phone)`. Telethon prompts on stdin for the SMS/app code and (if set) 2FA password.
- On success, prints `Session saved to: {path}`. Exits 0.
- On failure, prints the error and exits 1.
- MUST be run from user's local machine, not the VPS (datacenter-IP first-auth = ban trigger). Documented in README.

### Logging
- structlog configured for plain key-value console output at this phase
- Log levels from env: `TG_VOICE_LOG_LEVEL=INFO` default
- No privacy scrubbing yet — no transcripts to leak in Phase 1. That's Phase 5.

### Git / Repo Scaffolding
- `.gitignore` excludes: `.venv/`, `__pycache__/`, `*.session*`, `.env`, `.local/`, `dist/`, `build/`, `*.egg-info/`, `.ruff_cache/`, `.pytest_cache/`
- `.env.example` committed with placeholder values
- `README.md` with setup instructions (venv creation, pip install -e, how to fill .env, how to run login.py)
- `pyproject.toml` with PEP 621 metadata, optional dev extras, ruff + pytest config

### Claude's Discretion
- Exact ruff rule selection (default-ish: E, F, I, UP, B)
- Whether to include a smoke `__init__.py` test or wait for Phase 2 to bring pytest online
- README wording

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — greenfield. This phase creates the reusable foundation.

### Established Patterns
- Standard Python src-layout + pyproject.toml (PEP 621)
- pydantic-settings for env-driven typed config (industry-standard 2025+)
- Telethon idioms from official docs

### Integration Points
- Phase 2 will import `Config` and use `AudioPipeline` alongside the client
- Phase 3 will import `Config` and add `Transcriber` as a module-level singleton
- Phase 4 will import the `TelegramUserbot` wrapper and attach event handlers
- systemd unit (Phase 6) will invoke `python -m tg_voice_transcriber`, which for now just starts the client and idles

</code_context>

<specifics>
## Specific Ideas

- Users' first interaction is `scripts/login.py` — this must print clear instructions and verify session file creation at the end
- `__main__.py` for now: connects, prints `connected as @me`, sleeps on `client.run_until_disconnected()` so systemd has something to supervise even in Phase 1 (useful for manual smoke-testing on dev machine)
- `.env.example` must document how to obtain `API_ID` and `API_HASH` from my.telegram.org

</specifics>

<deferred>
## Deferred Ideas

- Event handlers, voice-note filter (Phase 4)
- Audio download + FFmpeg (Phase 2)
- Whisper model load (Phase 3)
- Queue + worker (Phases 4-5)
- FloodWait / retry (Phase 5)
- systemd service file (Phase 6)
- Structured privacy scrubbing in logs (Phase 5)
- Pytest tests (begin in Phase 2 once there's non-trivial logic to test)

</deferred>
