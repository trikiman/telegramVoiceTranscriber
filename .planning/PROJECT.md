# Telegram Voice Transcriber

## What This Is

A personal Telegram userbot that runs under the user's own account, auto-transcribes incoming and outgoing voice notes in one-on-one chats, and delivers a curated digest of messages from subscribed channels. Transcriptions appear as in-place reply edits in the original chat; digests arrive as summaries in Saved Messages. Built for someone who prefers text and gets overwhelmed by both voice messages and channel notifications.

## Core Value

Every DM voice note gets a fast, accurate transcript within seconds, and the user only sees channel posts that actually matter to them.

## Current Milestone: v1.1 — Smart Assistant

**Goal:** Add AI-powered channel digest so the user can follow many channels without notification overload.

**Target features:**
- Channel digest: batched LLM relevance filtering every 30 min, one summary message instead of 200 pings

## Requirements

### Validated

- ✓ Authenticate as user's Telegram account with persistent session — v1.0 Phase 1
- ✓ Listen for voice notes in 1-on-1 chats (incoming + outgoing) — v1.0 Phase 4
- ✓ Transcribe voice notes via Groq whisper-large-v3-turbo with 6-key rotation pool — v1.0 Phase 3/5
- ✓ Post transcripts as edited replies with ⏳ → final text flow — v1.0 Phase 4
- ✓ Privacy-safe logging (hashed chat IDs, no transcript content at INFO) — v1.0 Phase 5
- ✓ systemd deployment on Oracle VPS (158.101.214.234) — v1.0 Phase 6

### Active

- [ ] Background task collects new messages from user-selected channels into a buffer
- [ ] Every N minutes (configurable), batch buffer is sent to Groq LLM for relevance scoring
- [ ] LLM returns per-message scores against user's preferences, filtered to threshold
- [ ] Filtered digest delivered to user's configured chat (default Saved Messages)
- [ ] `/digest` command surface: setup, pause, resume, now, channels, prefs, stats, unsub
- [ ] User preferences persisted in SQLite (no external DB)
- [ ] Deduplication of near-duplicate posts across channels before scoring
- [ ] No back-fill on first subscribe — digest starts from "now"

### Out of Scope

- Group, channel, and supergroup chats — DMs only, reduces noise and keeps scope personal
- Video notes ("кружочки"), audio files, video files — only voice notes for v1, can revisit later
- Reply bot (forwarding model) — userbot approach covers the same need with better UX
- GPU acceleration — VPS is CPU-only, `small` model is fast enough on CPU
- Cloud transcription APIs (Groq, OpenAI, Google) — goal is free and private
- Whitelist/blocklist per chat — all DMs are in scope; revisit if noise becomes a problem
- Multi-user / multi-account support — single personal account only
- Telegram Mini App or custom UI — transcripts live in chat as text replies

## Context

- The user uses Telegram daily but dislikes voice messages; Telegram Premium offers transcription but is paid.
- Target deployment host is an existing Oracle Cloud Ubuntu VPS the user already SSHes into with a key at `e:\Projects\vless\oracle_vless_key` (host `158.101.214.234`, user `ubuntu`). The VPS already runs a vless service; this project must coexist without conflicts.
- Languages in scope are Russian and English. Voice notes from contacts may arrive in either.
- The user prefers a userbot (Telethon or Pyrogram) over a reply bot because it works transparently in every DM without forwarding.
- faster-whisper was chosen over vanilla Whisper for speed, over Vosk for quality, and over Groq free tier for privacy and no quota limits.
- Telegram userbot sessions require an API ID and API hash from https://my.telegram.org; first-login also needs the phone number and an SMS/app code.

## Constraints

- **Tech stack**: Python 3.10+, Telethon (userbot framework), faster-whisper with `small` model, FFmpeg for OGG→WAV conversion — chosen for best local free transcription quality in RU+EN on CPU.
- **Compatibility**: Must run on Ubuntu on the Oracle VPS (x86_64, CPU-only, limited RAM). `small` model was selected over `medium` to stay within VPS resources.
- **Security**: Telegram API credentials, session file, and any secrets must live only on the VPS and never be committed to git.
- **Privacy**: Audio must be processed locally on the VPS. No third-party transcription APIs.
- **Operational**: Must run as a systemd service with auto-restart and log rotation. Surviving host reboots is required.
- **Account safety**: Userbot must behave politely — avoid patterns that look like automation abuse (excessive editing, mass messaging) to reduce ban risk.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Userbot instead of reply bot | Transparent UX in every DM, no forwarding step, user asked for this explicitly | ✓ Good |
| Telethon over Pyrogram | Mature, widely used, good async support, good docs for event handlers | ✓ Good |
| Switched from local faster-whisper to Groq whisper-large-v3-turbo | 956 MB VPS couldn't run local whisper in reasonable time (2 min for 1-sec clip). Groq: 1-3 sec on free tier. Audio leaves VPS (acceptable — session already on a single VPS, privacy model is "trust the operator"). | ✓ Good (v1.0) |
| 6-key Groq rotation pool | Free-tier quota per key; rotating on 429 multiplies headroom ~6x without payment | ✓ Good (v1.0) |
| Transcript posted as reply in same chat | Matches user's stated preference | ✓ Good |
| Scope limited to DMs and voice notes only for v1 | Keeps first milestone tight and shippable | ✓ Good |
| Deploy as systemd service on Oracle VPS | Auto-restart, survives reboot, standard Linux tooling | ✓ Good |
| Single-user / personal use | Simplifies auth, storage, and deployment | ✓ Good |
| Use Llama 3.3 70B on Groq for v1.1 digest filtering | Same key pool, free tier supports 1M tokens/day per key; 30-min batching keeps cost near-zero | — Pending |
| SQLite for v1.1 digest state (user prefs, dedupe cache) | No external DB needed, fits on the VPS, backup with session file | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-12 after initialization*
