# Roadmap: Telegram Voice Transcriber

## Overview

From an empty repo to a hardened systemd-managed userbot on the Oracle VPS that auto-transcribes Russian and English voice notes in DMs and delivers AI-filtered channel digests. Two milestones shipped: v1.0 (voice transcription) and v1.1 (channel digest).

## Milestones

- ✅ **v1.0 MVP** — Phases 1-6 (shipped 2026-05-12)
- ✅ **v1.1 Channel Digest** — Phase 7 (shipped 2026-05-14)
- 🏃 **v1.2 VPN Trial Finder** — Phase 8 (planning)

## Phases

<details>
<summary>✅ v1.0 MVP (Phases 1-6) — SHIPPED 2026-05-12</summary>

- [x] **Phase 1: Bootstrap & Session** — Repo scaffold, typed config, Telethon client wrapper, interactive one-time login
- [x] **Phase 2: Audio Pipeline** — FFmpeg-based OGG/Opus → 16 kHz mono PCM converter with duration guards
- [x] **Phase 3: Transcription Engine** — Pivoted from local faster-whisper to Groq whisper-large-v3-turbo with 6-key pool rotation
- [x] **Phase 4: Event Wiring & Reply UX** — Listener + filter + queue + worker + placeholder/edit reply flow end to end
- [x] **Phase 5: Hardening** — Bounded queue drop policy, FloodWait + retry, graceful shutdown, structured privacy-safe logging, long-transcript splitting
- [x] **Phase 6: VPS Deployment** — Service user, filesystem layout, hardened systemd unit, journald verified, reboot survival — LIVE on 158.101.214.234

</details>

<details>
<summary>✅ v1.1 Channel Digest (Phase 7) — SHIPPED 2026-05-14</summary>

- [x] **Phase 7: Channel Digest (v1.1)** — LLM-filtered digest of subscribed channels, delivered to Saved Messages on a configurable schedule

</details>

## Phase Details

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

## Phase Details

### Phase 1: Bootstrap & Session
**Goal**: The project scaffold exists, dependencies install cleanly, typed configuration loads from env, and the user can perform a one-time interactive Telegram login that produces a reusable session file.
**Depends on**: Nothing (first phase)
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04
**Success Criteria** (what must be TRUE):
  1. `pip install -e .` succeeds in a clean Python 3.11 venv on both dev machine and Oracle VPS
  2. Running `python scripts/login.py` once from the user's local machine prompts for the SMS code (and 2FA password if set), then creates a `.session` file
  3. With the session file in place, the client connects without any interactive prompt and logs "connected as @me" with the user's account
  4. An invalid or expired session causes the client to exit with a non-zero status and a clear `AUTH_REQUIRED` log line instead of looping
  5. No secrets (API_ID, API_HASH, phone, session) exist in the repo; all come from `.env` or systemd `EnvironmentFile`
**Plans**: TBD

Plans:
- [ ] 01-01: Project scaffold, pyproject, typed Config, Telethon client wrapper, login script

### Phase 2: Audio Pipeline
**Goal**: Given raw Telegram voice-note bytes, produce 16 kHz mono signed-16-bit PCM suitable for faster-whisper, with duration guards and clean resource handling. No Telegram dependency — pure byte-in / byte-out.
**Depends on**: Phase 1 (for project scaffold and config)
**Requirements**: AUD-01, AUD-02, AUD-03, AUD-04, AUD-05
**Success Criteria** (what must be TRUE):
  1. `scripts/smoke.py fixture.ogg` prints the PCM byte count and the first few sample values for a known-good fixture
  2. Empty and near-silent fixtures (<1 second duration) are rejected without invoking FFmpeg-heavy conversion
  3. Oversized fixtures (>10 minutes) are rejected with a clear signal for the caller to post a "too long" reply
  4. All temporary audio data is released after conversion (no /tmp growth on repeat runs)
  5. Missing FFmpeg is detected at startup and fails fast with a readable error, not mid-voice-note
**Plans**: TBD

Plans:
- [ ] 02-01: FFmpeg subprocess wrapper with duration guards and fail-fast startup check

### Phase 3: Transcription Engine
**Goal**: A warm-loaded faster-whisper `small` multilingual model running on CPU with `int8` compute type, invoked off the event loop via a single-thread executor, auto-detecting Russian or English and suppressing whisper's known silence-hallucinations.
**Depends on**: Phase 1 (config and scaffold)
**Requirements**: TRN-01, TRN-02, TRN-03, TRN-04, TRN-05, TRN-06
**Success Criteria** (what must be TRUE):
  1. The model loads once at startup and does not reload between transcriptions
  2. `scripts/smoke.py ru-fixture.ogg` produces a Russian transcript and `scripts/smoke.py en-fixture.ogg` produces an English transcript, each tagged with the detected language
  3. Transcription calls are executed in the whisper-dedicated thread pool; running one does not block a concurrent `asyncio.sleep(0.1)` on the main loop
  4. A silent or sub-one-second fixture returns an empty or "(silence)" transcript rather than a hallucinated string
  5. Startup fails fast and loudly if the model cannot be loaded (missing cache, corrupted download, unsupported compute_type)
**Plans**: TBD

Plans:
- [ ] 03-01: WhisperModel warm-load, executor wrapper, VAD + language clamp, hallucination filter

### Phase 4: Event Wiring & Reply UX
**Goal**: End-to-end: a real voice note received (or sent) in a one-on-one Telegram chat triggers a `⏳ Transcribing…` placeholder reply, followed by an in-place edit with the final transcript. Filtering correctly ignores groups, channels, non-voice media, and messages older than the configured freshness window.
**Depends on**: Phases 1, 2, and 3
**Requirements**: LIST-01, LIST-02, LIST-03, LIST-04, LIST-05, LIST-06, RPL-01, RPL-02, RPL-03
**Success Criteria** (what must be TRUE):
  1. Sending a voice note to the userbot's own account (Saved Messages or a test DM) results in `⏳ Transcribing…` within two seconds, then an edit with the transcript when whisper finishes
  2. A voice note in a test group chat is ignored (no placeholder, no reply)
  3. A plain audio file sent in a one-on-one chat is ignored
  4. An outgoing voice note (sent by the user themselves in a DM) is transcribed the same way as incoming ones
  5. Voice notes whose `message.date` is older than 10 minutes are silently skipped
  6. Replies are threaded via `reply_to` to the original voice message and sent as plain text (no parse_mode)
**Plans**: TBD

Plans:
- [ ] 04-01: Event handler + filters + Job queue + worker loop + formatter + reply service (placeholder/edit flow)

### Phase 5: Hardening
**Goal**: The bot survives real-world conditions — bursts of voice notes, FloodWait responses, transient RPC errors, deleted-before-reply messages, oversized transcripts, and SIGTERM during in-flight transcription — while logging privacy-safely.
**Depends on**: Phase 4
**Requirements**: AUD-04, RPL-04, RPL-05, RPL-06, REL-01, REL-02, REL-03, REL-04, REL-05, REL-06, DEP-08, DEP-09
**Success Criteria** (what must be TRUE):
  1. A burst of 10 voice notes in one chat is processed in FIFO order with no OOM, no dropped replies, and no duplicate transcripts
  2. When the queue is full (simulated with a low `maxsize`), excess jobs are dropped with a WARNING log entry and no reply is posted for dropped jobs
  3. A simulated `FloodWaitError` of 5 seconds causes the worker to sleep and then complete; no aggressive retry loop occurs
  4. A simulated deleted-message scenario during placeholder editing does not crash the worker (swallowed `MessageIdInvalidError`)
  5. A transcript exceeding 4096 characters is split into multiple reply messages, each threaded to the voice note
  6. `systemctl stop` (or equivalent SIGTERM) waits for in-flight transcription to finish up to a grace period, then disconnects cleanly; no "⏳ Transcribing…" placeholder is left orphaned
  7. `journalctl -u tg-voice-transcriber` at default INFO level contains NO raw transcript text and NO raw chat_id/sender_id values; only hashed IDs and metadata appear
**Plans**: TBD

Plans:
- [ ] 05-01: Bounded queue + drop policy + FloodWait + retries + long-transcript split + deleted-msg swallow
- [ ] 05-02: Graceful shutdown + structured privacy-safe logging + correlation IDs

### Phase 6: VPS Deployment
**Goal**: The userbot runs on the Oracle Ubuntu VPS as a hardened systemd service under a dedicated unprivileged user, survives a host reboot, and has documented session/secret backup and re-auth procedures.
**Depends on**: Phase 5
**Requirements**: DEP-01, DEP-02, DEP-03, DEP-04, DEP-05, DEP-06, DEP-07, DEP-10
**Success Criteria** (what must be TRUE):
  1. A dedicated `tgbot` system user exists with home at `/var/lib/tg-voice-transcriber`, no shell, no login
  2. Code, env file, and session file live at their canonical paths with the documented permissions (`/opt/...`, `/etc/.../env` `0640 root:tgbot`, session `0600 tgbot:tgbot`)
  3. The faster-whisper model is pre-downloaded during deployment so `systemctl start` returns within a few seconds on first boot
  4. The systemd unit applies hardening directives (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `ReadWritePaths=/var/lib/tg-voice-transcriber`) and rate-limited restarts (`StartLimitBurst=5`)
  5. `systemctl status tg-voice-transcriber` reports `active (running)` after a manual VPS reboot, within 60 seconds of boot
  6. A written procedure in the README explains how to back up the session file + env file together, and how to re-authenticate if the session is invalidated
**Plans**: TBD

Plans:
- [ ] 06-01: Service user + filesystem layout + hardened systemd unit + model pre-download + journald verification + backup/re-auth docs

### Phase 7: Channel Digest (v1.1)
**Goal**: An LLM-filtered digest of user-selected channels delivered as a single summary message to Saved Messages every N minutes, so the user can follow many channels without notification overload.
**Depends on**: Phase 6 (deployment must be stable)
**Requirements**: DIG-01, DIG-02, DIG-03, DIG-04, DIG-05, DIG-06, DIG-07, DIG-08, DIG-09
**Success Criteria** (what must be TRUE):
  1. User runs `/digest setup` in Saved Messages and is walked through selecting tracked channels, writing preferences, setting threshold, choosing delivery chat, and picking frequency
  2. The bot records selected channels' new posts into a buffer as they arrive (zero LLM calls at ingest)
  3. On a configurable schedule (default every 30 min), the bot batches the buffered posts into one Groq LLM call and receives per-post scores + one-line summaries
  4. Posts scoring at or above the user's threshold are formatted into a digest message (grouped by channel, with direct links to originals) and sent to the configured delivery chat
  5. Digest messages are skipped entirely if the batch contains no posts above threshold (no empty digests)
  6. Commands `/digest pause`, `/digest resume`, `/digest now`, `/digest channels`, `/digest prefs`, `/digest stats`, `/digest unsub @channel` work as documented
  7. Preferences, tracked-channel list, and dedupe cache persist across service restarts (stored in SQLite under `/var/lib/tg-voice-transcriber/digest.db`)
  8. First subscription to a channel does not back-fill history — only posts from "now onwards" are scored
  9. Token usage stays well under Groq free-tier quota for a user with ~50 tracked channels producing ~10k posts/day
**Plans**: TBD

Plans:
- [ ] 07-01: SQLite schema + Groq LLM chat client + channel ingest listener + batched scoring task + digest formatter + `/digest` command handlers

### Phase 8: VPN Trial Finder (v1.2)
**Goal**: Identify VPN/proxy trial offers (10+ days for 0-1 RUB) from ads, auto-start them, mute them, and organize them into a Telegram folder.
**Depends on**: Phase 7
**Requirements**: FINDER-01, FINDER-02, FINDER-03, FINDER-04, FINDER-05, FINDER-06, FINDER-07
**Success Criteria** (what must be TRUE):
  1. Telegram sponsored ads and proxy sponsor channels are fetched properly.
  2. LLM accurately discriminates offers meeting the 10+ days and 0-1 RUB constraints.
  3. Qualifying bot URLs are sent a `/start` payload.
  4. Qualifying bots are successfully muted.
  5. Qualifying bots are automatically added to the configured folder (e.g., "10 дней vpn").
**Plans**: TBD

Plans:
- [ ] 08-01: Code review and verification of existing `finder` implementations.
- [ ] 08-02: Live verification and final wiring.

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Bootstrap & Session | v1.0 | 1/1 | Complete | 2026-05-12 |
| 2. Audio Pipeline | v1.0 | 1/1 | Complete | 2026-05-12 |
| 3. Transcription Engine | v1.0 | 1/1 | Complete | 2026-05-12 |
| 4. Event Wiring & Reply UX | v1.0 | 1/1 | Complete | 2026-05-12 |
| 5. Hardening | v1.0 | 1/1 | Complete | 2026-05-12 |
| 6. VPS Deployment | v1.0 | 1/1 | Complete | 2026-05-12 |
| 7. Channel Digest | v1.1 | 1/1 | Complete | 2026-05-14 |
| 8. VPN Trial Finder | v1.2 | 0/2 | Planning | |
