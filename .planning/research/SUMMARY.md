# Research Summary

**Project:** Telegram Voice Transcriber (personal userbot, faster-whisper local, Oracle VPS)
**Milestone:** v1.0 (greenfield)
**Researched:** 2026-05-12
**Overall confidence:** HIGH on architecture and stack, MEDIUM on specific ban-risk heuristics

---

## Key Findings

### Stack (verified)
- **Python 3.11** + **Telethon 1.43.x** (pin `<2.0` — 2.0 is alpha) + **faster-whisper 1.2.x** + **CTranslate2 4.7.x** (transitive) + **FFmpeg** (apt) + **systemd**.
- ARM64 wheels confirmed available for CTranslate2 — Oracle Ampere A1 free shape is viable.
- `compute_type="int8"` on CPU — never `float16`.
- Multilingual `small` variant (not `.en`) for RU + EN.

### Feature Table Stakes
Non-negotiable for v1:
1. Listen to voice notes in 1-on-1 chats (incoming + outgoing), filter out groups/channels
2. Async queue + single worker (bursts don't OOM)
3. Warm model loaded once at startup
4. Placeholder `⏳ Transcribing...` edited to final text (eliminates perceived latency)
5. RU/EN auto-detect with hallucination guards (empty/too-short audio)
6. Duration cap (reject >10 min audio)
7. Session persistence + reauth-on-invalidation
8. systemd service with `Restart=on-failure` + rate-limited restarts
9. FloodWait handling (ban avoidance)
10. Markdown escape on output, 4096-char split
11. Graceful shutdown on SIGTERM
12. Privacy-safe logging (metadata only, no transcript content at INFO)

### Architecture
- **Single Python process, single asyncio loop, 1 worker coroutine, 1-thread executor for Whisper.**
- Do NOT run Whisper inline in handlers (blocks event loop → bot silent → reconnect storm).
- Do NOT use multiple processes/workers (duplicates 500 MB model, no throughput gain).
- Data flow: Telethon event → filter → `asyncio.Queue` (maxsize=10) → worker → download → FFmpeg pipe → faster-whisper (in thread) → formatter → reply with `reply_to=voice_msg_id`.

### Watch Out For (top 5 pitfalls)
1. **First-login IP matters.** Do initial Telethon login from your home IP, not the VPS — first auth from a datacenter IP is a ban trigger. Transfer session file after.
2. **Session file = account takeover.** `chmod 600`, owner `tgbot:tgbot`, path `/var/lib/tg-voice-transcriber/`, never in repo, never in snapshots.
3. **Whisper hallucinations on silence.** "спасибо за просмотр" / "Thanks for watching" on <1s or silent clips. Guard with duration check + VAD + min-length check on output.
4. **Calling `model.transcribe()` from async handler.** Freezes event loop, kills keepalive. Always `run_in_executor`.
5. **Wrong Oracle shape.** 1 GB E2.1.Micro OOMs on `small` model. Use Ampere A1 free (4 OCPU / 24 GB) instead.

### Cost Posture
Stays free forever on Oracle Ampere A1 free tier. Risks of silent cost creep:
- Picking E2.1.Micro (OOMs, user upgrades to paid)
- Model cache bloat (pin one model)
- Log/journal growth (default systemd-journald rotation is fine, verify)

---

## 6-Phase Build Order

Standard granularity, sequential with one parallel pair (Phases 2 + 3 share no Telegram surface).

| # | Phase | Goal | Key deliverable |
|---|-------|------|-----------------|
| 1 | **Bootstrap + Session** | Repo, venv, typed config, Telethon client wrapper, interactive login script | Can log in once and print "connected as @me" |
| 2 | **Audio Pipeline** | FFmpeg subprocess wrapper, OGG → 16 kHz mono PCM | `scripts/smoke.py fixture.ogg` prints byte count and first samples |
| 3 | **Transcription Engine** | faster-whisper small int8 CPU, warm-loaded, RU/EN auto-detect, VAD | Smoke script transcribes Russian + English fixtures |
| 4 | **Event Wiring** | Handler + queue + worker + formatter + reply service; 1-on-1 filter; incoming + outgoing | Working bot locally: real voice notes get transcribed + replied |
| 5 | **Hardening** | Bounded queue drop policy, FloodWait retry, placeholder-edit flow, graceful shutdown, structured privacy logs, message-length split | Survives burst of 10 voice notes and simulated network blip |
| 6 | **VPS Deployment** | Service user, `/opt` + `/var/lib` + `/etc` layout, systemd unit with hardening, journalctl verified, session backup procedure | `systemctl status` green on Oracle VPS, survives reboot |

**Parallelizable:** 2 and 3 can run in parallel once their interfaces (bytes in → text out) are agreed. Phases 1 → 4, 2+3 → 4, 4 → 5 → 6 are sequential.

---

## Requirements Implications

When defining REQUIREMENTS.md, structure by category to match the 6-phase build. Suggested categories:

- **Auth / Session (AUTH-xx):** login, session persistence, reauth detection
- **Listener (LIST-xx):** 1-on-1 filter, voice-only filter, direction toggle (incoming/outgoing)
- **Audio (AUD-xx):** download, OGG→PCM conversion, duration cap, empty-audio guard
- **Transcription (TRN-xx):** warm-loaded model, RU/EN auto-detect, VAD, int8 compute
- **Reply (RPL-xx):** placeholder + edit, reply_to, markdown escape, length split
- **Reliability (REL-xx):** queue + worker, FloodWait handling, retries, graceful shutdown, skip-old-messages
- **Deployment (DEP-xx):** systemd service, env config, service user + filesystem layout, privacy-safe logging

---

## Open Items / [VERIFY] at Plan Time

Defer verification to the phase that touches each:

- Phase 3: current `language_detection_threshold` / `vad_filter` param syntax in installed faster-whisper
- Phase 3: int8 vs int8_float32 quality+speed on target VPS shape
- Phase 6: confirm `aarch64` wheel present for pinned `ctranslate2` version at deploy time
- Phase 6: Oracle image current default Ubuntu version + whether ffmpeg is in default repos
- Phase 6: `MemoryDenyWriteExecute=` compatibility with CTranslate2 JIT paths

These are tactical validations, not architectural risks.

---

*Research synthesized from STACK.md, FEATURES.md, ARCHITECTURE.md, PITFALLS.md.*
