# Telegram Voice Transcriber

## What This Is

A personal Telegram userbot that runs under the user's own account and automatically transcribes incoming and outgoing voice notes in one-on-one chats. Transcriptions are posted as replies in the same chat so both sides can read them without relying on Telegram Premium. Built for someone who prefers text and receives voice messages in Russian and English.

## Core Value

Every DM voice note (incoming and outgoing) gets a readable Russian/English transcript posted as a reply within seconds, without any paid service.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Authenticate as the user's Telegram account (userbot, not bot API) and persist the session across restarts
- [ ] Listen for new voice notes in one-on-one chats only (skip groups, channels, supergroups)
- [ ] Transcribe incoming voice notes from the other party
- [ ] Transcribe outgoing voice notes the user sends themselves
- [ ] Support Russian and English with automatic language detection per message
- [ ] Run transcription locally with faster-whisper using the `small` model
- [ ] Post the transcript as a reply to the original voice note in the same chat
- [ ] Handle voice notes longer than a few seconds without blocking other messages (queue/concurrency)
- [ ] Deploy and run on the Oracle Ubuntu VPS (158.101.214.234) as a systemd service that auto-restarts and survives reboots
- [ ] Keep transcription fully local — audio never leaves the VPS
- [ ] Log errors and transcription outcomes to a file the user can tail

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
| Userbot instead of reply bot | Transparent UX in every DM, no forwarding step, user asked for this explicitly | — Pending |
| Telethon over Pyrogram | Mature, widely used, good async support, good docs for event handlers | — Pending |
| faster-whisper `small` model | Best quality-to-resource ratio on CPU-only VPS; handles RU + EN well | — Pending |
| Transcript posted as reply in same chat | Matches user's stated preference over private log or Saved Messages | — Pending |
| Scope limited to DMs and voice notes only for v1 | Keeps first milestone tight and shippable | — Pending |
| Deploy as systemd service on Oracle VPS | Auto-restart, survives reboot, standard Linux tooling | — Pending |
| Single-user / personal use | Simplifies auth, storage, and deployment | — Pending |

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
