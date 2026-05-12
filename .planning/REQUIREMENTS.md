# Requirements — Milestone v1.0

**Project:** Telegram Voice Transcriber
**Milestone:** v1.0 — personal userbot for DM voice-note transcription
**Last updated:** 2026-05-12

---

## v1.0 Requirements

### Authentication & Session (AUTH)

- [ ] **AUTH-01**: User can authenticate as their Telegram account using API_ID, API_HASH, and phone number provided via environment variables
- [ ] **AUTH-02**: User performs a one-time interactive login (SMS/app code, optional 2FA password) from their local machine, which creates a reusable session file
- [ ] **AUTH-03**: The userbot reuses the saved session file on every subsequent start without prompting for credentials
- [ ] **AUTH-04**: On invalid or expired session (e.g. `AuthKeyError`, `UserDeactivatedError`), the userbot exits with a non-zero status code and a clear log message, without restart-looping

### Listener & Filtering (LIST)

- [ ] **LIST-01**: The userbot listens for new messages containing a voice note (`DocumentAttributeAudio(voice=True)`) in one-on-one chats with other users
- [ ] **LIST-02**: The userbot transcribes incoming voice notes sent by the other party
- [ ] **LIST-03**: The userbot transcribes outgoing voice notes sent by the user themselves
- [ ] **LIST-04**: The userbot ignores voice notes in groups, supergroups, and channels
- [ ] **LIST-05**: The userbot ignores non-voice media (regular audio files, video files, video notes, documents, photos) in v1
- [ ] **LIST-06**: The userbot skips voice notes whose `message.date` is older than a configurable threshold (default 10 minutes) to avoid spam on reconnect after downtime

### Audio Pipeline (AUD)

- [ ] **AUD-01**: The userbot downloads the voice-note payload from Telegram into memory (no permanent disk storage)
- [ ] **AUD-02**: The userbot converts the OGG/Opus payload to 16 kHz mono signed-16-bit PCM using FFmpeg
- [ ] **AUD-03**: The userbot rejects voice notes shorter than a configurable minimum (default 1 second) without calling the transcription engine
- [ ] **AUD-04**: The userbot rejects voice notes longer than a configurable maximum (default 10 minutes) and posts a short "too long to transcribe" reply instead
- [ ] **AUD-05**: The userbot deletes any temporary audio data after transcription (successful or failed)

### Transcription Engine (TRN)

- [ ] **TRN-01**: The userbot loads the faster-whisper `small` multilingual model once at startup and keeps it resident in memory
- [ ] **TRN-02**: The userbot runs all transcription calls through a dedicated single-thread executor so the asyncio event loop is never blocked
- [ ] **TRN-03**: The userbot uses `compute_type="int8"` on CPU (never `float16`)
- [ ] **TRN-04**: The userbot auto-detects language per voice note, clamped to Russian or English
- [ ] **TRN-05**: The userbot uses built-in voice-activity-detection (`vad_filter=True`) to strip silence and reduce hallucinations
- [ ] **TRN-06**: The userbot suppresses empty or whitespace-only transcripts and known-hallucination phrases; such cases post "(silence)" or are skipped

### Reply UX (RPL)

- [ ] **RPL-01**: Immediately on receiving a qualifying voice note, the userbot sends a "⏳ Transcribing…" placeholder reply in the same chat, threaded to the original voice message via `reply_to`
- [ ] **RPL-02**: When transcription completes, the userbot edits the placeholder in place with the final transcript (single message, no duplicate reply)
- [ ] **RPL-03**: The userbot sends replies as plain text (no Markdown/HTML parse mode) so arbitrary transcript characters cannot break formatting
- [ ] **RPL-04**: Transcripts longer than 4096 characters are split across multiple messages (each a reply to the voice note)
- [ ] **RPL-05**: On unrecoverable transcription errors, the placeholder is edited to "❌ transcription failed" rather than left as "⏳" or deleted
- [ ] **RPL-06**: The userbot silently swallows `MessageIdInvalidError` if the user deletes the original voice note before the reply is sent or edited

### Reliability (REL)

- [ ] **REL-01**: Voice-note jobs are placed on a bounded `asyncio.Queue` (default `maxsize=10`) and consumed by a single worker coroutine in FIFO order
- [ ] **REL-02**: When the queue is full, new jobs are dropped with a log entry; no reply is sent for dropped jobs by default
- [ ] **REL-03**: On `FloodWaitError` from Telegram API calls, the userbot sleeps for the duration Telegram specifies, then continues, without aggressive retries
- [ ] **REL-04**: Transient failures during voice download or reply send are retried up to 2 times with exponential backoff
- [ ] **REL-05**: Telethon's automatic reconnect on network blips is left enabled and reconnect events are logged at INFO level
- [ ] **REL-06**: On SIGTERM (from systemd stop/restart), the userbot stops accepting new jobs, waits up to a configurable grace period for in-flight transcription to finish, then disconnects cleanly

### Deployment & Operations (DEP)

- [ ] **DEP-01**: The userbot runs on the user's existing Oracle Cloud Ubuntu VPS as a systemd service under a dedicated unprivileged system user (`tgbot`)
- [ ] **DEP-02**: The systemd unit uses `Restart=on-failure` with rate-limited restarts (`StartLimitBurst=5`, `StartLimitIntervalSec=300`) to prevent restart-storms on persistent errors
- [ ] **DEP-03**: The systemd unit applies standard hardening directives (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `ReadWritePaths=/var/lib/tg-voice-transcriber`)
- [ ] **DEP-04**: Secrets (API_ID, API_HASH, phone, session path) are loaded from an `EnvironmentFile` at `/etc/tg-voice-transcriber/env` with permissions `0640 root:tgbot`; none of these values live in the repo
- [ ] **DEP-05**: The session file lives at `/var/lib/tg-voice-transcriber/userbot.session` with permissions `0600 tgbot:tgbot`
- [ ] **DEP-06**: The faster-whisper model is pre-downloaded during deployment (not at first startup) so systemd does not time out on first boot
- [ ] **DEP-07**: All logs go to stdout/stderr and are captured by journald; the userbot logs at INFO or above by default
- [ ] **DEP-08**: Log entries never contain transcript text at INFO or above; transcript content is only logged at DEBUG level behind an explicit `LOG_TRANSCRIPTS=true` flag (off by default)
- [ ] **DEP-09**: Chat IDs and sender IDs are salted-hashed in logs so journalctl output cannot reverse-map to specific contacts
- [ ] **DEP-10**: The userbot survives VPS reboot and is running again within 60 seconds of boot

---

## Future Requirements (v1.1+)

Deferred from v1 by explicit scoping. Carry these forward as candidates for the next milestone.

- Transcription of video notes ("кружочки") and generic audio/video file attachments
- Support for groups, supergroups, and channels (with per-chat whitelist to manage volume and ban risk)
- `/mute` and `/unmute` commands and per-chat preference store
- Segment timestamps on long transcripts (>1 min)
- Language tag prefix in replies (`🇷🇺` / `🇬🇧` or `[ru]` / `[en]`)
- Prometheus/Grafana metrics endpoint
- Word-level confidence markers on low-certainty segments
- Per-chat rate limiting beyond the global queue

---

## Out of Scope (explicit exclusions)

| Exclusion | Reason |
|---|---|
| GPU inference | Oracle VPS is CPU-only; `small` model on int8 CPU is fast enough for v1 |
| Cloud transcription APIs (OpenAI, Groq, Deepgram) | Goal is free and private; audio must never leave the VPS |
| Auto-translation of transcripts | Different problem, noisy quality on unclean audio, separate milestone if ever |
| Long-term storage of audio files or transcripts (DB or files) | Privacy liability; transcript lives only as a Telegram message |
| Logging transcripts to a separate archive channel or Saved Messages | Doubles privacy exposure surface |
| Speaker diarization (pyannote / WhisperX) | Complexity + RAM not justified for 1-on-1 voice notes |
| Multi-account / multi-user in one process | Session complexity, blast radius on compromise; one systemd unit per account if ever needed |
| Web UI / HTTP control plane | Attack surface with no v1 need |
| Read-receipt automation (`ReadHistoryRequest`) | Common userbot ban trigger |
| Auto-deleting original voice notes after transcription | Destructive, unexpected, never done to others' messages |
| Streaming partial transcripts (multi-edit) | Edit rate limits + no meaningful mid-file partials on CPU whisper |
| Redacting "sensitive" words in the transcript | False sense of privacy — whisper transcribed them regardless |

---

## Traceability

(Filled in by the roadmapper — each REQ-ID maps to exactly one phase.)

| REQ-ID | Phase |
|---|---|
| AUTH-01 … AUTH-04 | — |
| LIST-01 … LIST-06 | — |
| AUD-01 … AUD-05 | — |
| TRN-01 … TRN-06 | — |
| RPL-01 … RPL-06 | — |
| REL-01 … REL-06 | — |
| DEP-01 … DEP-10 | — |

---
*Requirements for milestone v1.0 — Telegram Voice Transcriber*
