# Telegram Voice Transcriber

A personal Telegram userbot that transcribes voice notes in 1-on-1 chats using faster-whisper running locally. Built to be free, private, and to deploy on a personal Ubuntu VPS.

## Status

**Milestone v1.0** — in progress. Phase 1 (scaffold + session) is the first implemented phase; the full 6-phase roadmap lives in `.planning/ROADMAP.md`.

## What it does (once all phases are built)

- Runs under your own Telegram account (userbot, not bot API)
- Listens for voice notes in 1-on-1 chats (incoming and outgoing)
- Transcribes them with faster-whisper `small` (multilingual, RU + EN auto-detect) on CPU
- Posts the transcript as a reply to the original voice message — placeholder `⏳ Transcribing…` first, edited to the final text when whisper finishes

## Requirements

- Python 3.11 (3.12 works; avoid 3.13 until ML wheels catch up)
- FFmpeg on PATH (`apt install ffmpeg` on Ubuntu)
- Telegram API credentials from https://my.telegram.org (API_ID + API_HASH)
- Your phone number

## Setup

```bash
# 1. Clone and create venv
git clone <this-repo> telegram-voice-transcriber
cd telegram-voice-transcriber
python3.11 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install
pip install --upgrade pip wheel
pip install -e '.[dev]'

# 3. Configure
cp .env.example .env
# Open .env and fill in:
#   TG_VOICE_API_ID
#   TG_VOICE_API_HASH
#   TG_VOICE_PHONE
```

## One-time login (run from your LOCAL machine, not the VPS)

**Important:** the very first Telegram login should come from your usual IP — doing it from a datacenter IP is a well-known ban trigger for userbots. After `.local/userbot.session` is created, you can copy it to the VPS via `scp`.

```bash
python scripts/login.py
# You'll be prompted for the login code Telegram sends to your app/SMS.
# If you have 2FA enabled, also for your password.
# On success:  Session saved to: /abs/path/.local/userbot.session
```

## Run it (Phase 1 only logs in and idles)

```bash
python -m tg_voice_transcriber
# Expected: a log line "connected as @yourusername", then idle.
# Ctrl+C to stop.
```

Later phases will add the voice-note listener, audio pipeline, transcription, reply UX, hardening, and systemd deployment — see `.planning/ROADMAP.md`.

## Repo layout

```
src/tg_voice_transcriber/   Package (config, client, logging, future handlers)
scripts/                    Standalone scripts (login.py, smoke tools)
.planning/                  GSD project state (PROJECT.md, ROADMAP.md, phases)
.env.example                Config template (copy to .env, never commit .env)
.gitignore                  Excludes secrets, venvs, session files
pyproject.toml              Package metadata + tooling config
```

## Security notes

- `.env` and `*.session*` are gitignored. Verify `git status` never shows them.
- The session file is equivalent to your account — anyone with it can read and write as you. Keep it `chmod 600`.
- Never post your API_HASH or session file publicly.

## Next steps

Once Phase 1 ships, Phase 2 adds the FFmpeg audio pipeline, Phase 3 adds faster-whisper, and so on. Run the GSD workflow (`/gsd-plan-phase 2`) or just keep reading `.planning/ROADMAP.md`.
