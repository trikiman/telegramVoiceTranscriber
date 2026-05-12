# Feature Landscape

**Domain:** Personal Telegram userbot for local voice-note transcription (Russian + English, faster-whisper small, CPU, Oracle VPS, 1-on-1 chats only)
**Researched:** 2026-05-12
**Confidence:** MEDIUM — based on domain knowledge of Telethon/Pyrogram and faster-whisper behavior. Items flagged [VERIFY] warrant a web check before freezing requirements.

---

## Table Stakes

Features without which the userbot is useless or actively annoying.

| # | Feature | Complexity | Why it's table stakes | Notes |
|---|---|---|---|---|
| TS-1 | **Listen for voice messages in 1-on-1 chats (incoming + outgoing)** | Easy | Core trigger. Filter: `MessageMediaDocument` with `DocumentAttributeAudio(voice=True)` in `User` peers only. | Must explicitly exclude `Chat`/`Channel` peers. Outgoing needs `outgoing=True` in Telethon `NewMessage` filter. |
| TS-2 | **Download voice payload to local disk (or memory) before transcription** | Easy | Whisper needs a file/stream. Telegram voice notes are Opus in OGG container. | faster-whisper accepts file path directly; ffmpeg decodes OGG/Opus. |
| TS-3 | **Post transcript as a reply to the original voice message** | Easy | Without reply threading the transcript loses context. | Use `reply_to=message.id`. Same chat, not a separate log. |
| TS-4 | **Async processing queue (serialize whisper jobs)** | Medium | faster-whisper small on CPU uses ~1-2 GB RAM and 1 core per job. Bursts OOM or thrash. | `asyncio.Queue` + single worker task. FIFO preserves reply order. |
| TS-5 | **Session auth + persistent session file** | Easy | Must not prompt for SMS code on every restart. | First-run interactive login; systemd restarts reuse session. `0600` perms, outside repo. |
| TS-6 | **Graceful reauth / session-expired handling** | Medium | Telegram can invalidate sessions. Without handling, service silently dies. | Catch `AuthKeyError`/`UserDeactivatedError`, exit non-zero so systemd doesn't restart-loop. |
| TS-7 | **Handle empty / too-short / silent audio** | Easy | Whisper hallucinates ("спасибо за просмотр", "Thanks for watching") on <1s or silent clips. | Check `DocumentAttributeAudio.duration`; if <1s, skip. Also check transcript length ≥ N chars. [VERIFY] exact hallucination strings. |
| TS-8 | **Language auto-detect (RU/EN) or explicit language list** | Easy | Hardcoding language mistranscribes the other. Small model is decent at both. | `language=None` for auto. Log detected language. |
| TS-9 | **Systemd service with auto-restart + journald logs** | Easy | Reboot/blip/crash must not kill the bot silently. | `Restart=on-failure`, `RestartSec=10`, `StandardOutput=journal`. Rate-limit restarts. |
| TS-10 | **FloodWait handling** | Medium | Userbots held to stricter limits than bot API. Ignoring gets account temp-banned. | Telethon has built-in `flood_sleep_threshold`; for longer waits, catch `FloodWaitError` and sleep. |
| TS-11 | **Network disconnect / reconnect resilience** | Easy | VPS network flaps; Telegram closes idle connections. | Telethon/Pyrogram auto-reconnect is default on; don't suppress it. Log reconnects. |
| TS-12 | **Don't transcribe own outgoing transcripts (loop prevention)** | Easy | Bot's reply is text not voice — no loop in practice. Called out for future safety. | Trivial because filter is `voice=True`. |
| TS-13 | **Cleanup of downloaded audio after transcription** | Easy | Disk fills up on small VPS otherwise. | `try/finally` unlink, or `tempfile.NamedTemporaryFile`. |
| TS-14 | **Config via env vars or single config file (API_ID, API_HASH, model path, etc.)** | Easy | systemd means no interactive config edits. Secrets not in repo. | `.env` + `python-dotenv`, or systemd `EnvironmentFile=`. Session path, model, device, compute_type configurable. |
| TS-15 | **Max-duration cap (reject pathological audio)** | Easy | 2-hour voice note pegs CPU for 20+ min, queue backs up. | Configurable cap (e.g. 10 min). Over cap → reply "(too long to transcribe, >10 min)" without running whisper. |

---

## Differentiators

Quality-of-life features. Each one noticeably improves UX.

| # | Feature | Complexity | When worth it | Notes |
|---|---|---|---|---|
| D-1 | **"⏳ Transcribing…" placeholder, then edit-in-place with result** | Easy | Always — 5-30s silence on CPU feels broken | Send placeholder → run whisper → `edit_message` with final text. **Promote to v1.** |
| D-2 | **Language tag in output (e.g. `🇷🇺 …` or `[ru] …`)** | Easy | For mixed RU/EN chats | Map language code to flag or prefix. |
| D-3 | **Retry on whisper failure / transient error** | Easy | Yes — ffmpeg hiccups happen | 1-2 retries with backoff; then post "(transcription failed)". |
| D-4 | **Timestamps per segment** (e.g. `[00:05] …`) | Medium | For long voice notes (>1 min) | faster-whisper returns segments. Gate on duration. |
| D-5 | **Word-level confidence / low-confidence marker** | Medium | Marginal | `word_timestamps=True` slower; probably defer. |
| D-6 | **Rate limiting per chat (e.g. max 1 transcription per 3s per chat)** | Easy | Reduces ban risk for bursty chats | Per-chat timestamp map + sleep. |
| D-7 | **Global concurrency cap on whisper (expose as config)** | Easy | Makes queue behavior explicit | `WHISPER_MAX_CONCURRENCY=1` default. |
| D-8 | **Warm model (load once at startup, keep in memory)** | Easy | Essential-adjacent — model load is 2-4s | `WhisperModel(...)` as module-global. **Promote to v1.** |
| D-9 | **VAD (voice activity detection) preprocessing** | Medium | Yes — reduces hallucinations on near-silent clips | `vad_filter=True`. Low effort, high quality gain. **Recommend v1.** [VERIFY] API signature. |
| D-10 | **Compute type / precision tuning (`int8` on CPU)** | Easy | Yes — 2-4× faster on CPU | `compute_type="int8"`. [VERIFY] for Oracle Ampere vs x86. |
| D-11 | **Handling of ARM vs x86 (Oracle Ampere A1)** | Medium | Depends on chosen VPS | Document working install recipe. |
| D-12 | **Transcript-too-long handling (>4096 chars)** | Easy | Yes — Telegram limit | Split into parts, or truncate with "(…truncated)". |
| D-13 | **Markdown/formatting escape** | Easy | Yes — transcripts break Markdown parsing | Send as plain text (no parse_mode). **Promote to v1.** |
| D-14 | **Deleted-message handling** | Easy | Yes — user deletes before transcription completes | Catch `MessageIdInvalidError`, swallow. |
| D-15 | **Prometheus/health metrics endpoint** | Medium | Only if already running Prometheus | Defer. |
| D-16 | **Per-chat "mute" toggle via command** | Medium | Nice escape hatch | Contradicts "no whitelist/blocklist" v1 scope. Defer. |
| D-17 | **Translate to target language** | Medium | Explicitly out of scope | Defer. |
| D-18 | **Graceful shutdown (drain queue on SIGTERM)** | Easy | Yes — prevents orphan `⏳` placeholders | asyncio signal handler. **Promote to v1.** |
| D-19 | **Don't transcribe very old voice notes on reconnect** | Easy | Yes — avoids backlog spam | Skip if `message.date` older than 10 min. |
| D-20 | **Model download/caching handled on first boot** | Easy | Don't do it under systemd timeout | Pre-download in setup, or generous `TimeoutStartSec`. |
| D-21 | **Structured logging with hashed chat id, not raw id** | Easy | Privacy — journald ends up in backups | Low effort, day-1 habit. |

**Promotion candidates (v1 feels broken without them):** D-1 (placeholder+edit), D-8 (warm model), D-13 (markdown escape), D-18 (graceful shutdown).

---

## Anti-Features

Features that seem useful but should explicitly NOT be built.

| Anti-feature | Why avoid | What to do instead |
|---|---|---|
| **Log transcripts to archive channel / saved messages** | Privacy nightmare — doubles leak surface, creates searchable archive | Transcripts live only as in-chat replies. Forward manually if needed. |
| **Auto-translate transcripts** | Out of scope; translation quality on noisy speech is bad | Source-language transcript only. |
| **Store audio files long-term** | Disk bloat, privacy liability; Telegram already stores them | Delete temp files immediately. |
| **Store transcripts in a database** | Same privacy liability, no v1 use case | Ephemeral. |
| **Transcribe in groups / channels / supergroups** | Out of scope. Higher ban risk, consent issues | Filter to `User` peer only. Assert + log if somehow reaches handler. |
| **Read receipts / mark-as-read automation** | Classic ban signal for userbots | Let Telegram's default read behavior apply. |
| **Auto-transcribe video notes in v1** | Out of scope | Defer to later milestone. |
| **Sentiment analysis / summary / "smart" extras** | Feature creep | Plain transcript. |
| **Redact "sensitive" words client-side** | False security — whisper transcribed them anyway | Mute per-chat (future) if privacy matters. |
| **Multi-account in same process** | Session complexity, blast radius on compromise | One userbot per systemd unit. |
| **Run whisper on GPU / call cloud APIs** | Out of scope | Local CPU only. |
| **Expose web UI / HTTP control plane** | Another attack surface | systemd + journald for control. |
| **Auto-delete original voice note** | Destructive, unexpected | Never delete others' messages. |

---

## Deferred to Future Milestones

| Feature | Rationale |
|---|---|
| Group / supergroup / channel support | Higher ban risk, consent issues, volume explosion. Needs whitelist + rate limits first. |
| Video note ("кружочки") transcription | Requires ffmpeg audio extraction step. Easy add once v1 stable. |
| Audio file / music / forwarded voice transcription | Needs duration cap tuning + explicit trigger (e.g. reply "/transcribe"). |
| Per-chat whitelist / blocklist | Useful once group support lands. Not needed in 1-on-1. |
| `/mute` or `/unmute` commands | Requires per-chat state store. |
| Multi-account / multi-session | Session isolation, separate systemd units are probably the right answer. |
| GPU inference / larger model (medium, large-v3) | Requires different VPS class or cloud GPU. Small usually fine for conversational RU+EN. |
| Cloud API fallback (OpenAI, Deepgram) | Adds cost, privacy tradeoff — explicit opt-in. |
| Speaker diarization | pyannote/WhisperX add complexity. 1-on-1 ROI low. |
| Translation of transcripts | Separate pipeline. |
| Summary / action-item extraction | Needs LLM call. |
| Prometheus/Grafana observability | journald sufficient. |
| Web UI / dashboard | Anti-feature for personal userbot. |
| Transcript archival / search | Privacy sensitive; needs explicit design. |

---

## Feature Dependencies

```
TS-1 (listen voice) ──┬──> TS-2 (download) ──> TS-4 (queue) ──> TS-7 (short/empty guard) ──> whisper ──> TS-3 (reply)
                      │                                                                         │
                      └──> TS-12 (loop prevention)                                               └──> TS-13 (cleanup)

TS-5 (session)  ──> TS-6 (reauth)  ──> TS-9 (systemd)
TS-9 (systemd) ──> TS-11 (reconnect) ──> TS-10 (FloodWait)

D-1 requires TS-3 + edit_message + TS-4 (knows when transcription starts)
D-3 requires TS-4 (retries don't head-of-line block)
D-4 requires whisper segments + TS-15 (duration cap gate)
D-8 is prerequisite for acceptable latency of D-1
D-9 depends on TS-7 (VAD can return empty)
D-12 depends on TS-3
D-13 required for correctness of TS-3 — promote to table stakes
D-18 depends on TS-4
D-19 depends on TS-11

TS-14 (config) upstream of almost everything
TS-15 (duration cap) gates whisper path
```

**Promotion candidates:** D-1, D-8, D-13, D-18 should be v1 must-ship.

---

## Incoming vs Outgoing Voice Note Handling

| Aspect | Incoming (contact → you) | Outgoing (you → contact) |
|---|---|---|
| Trigger | `NewMessage(incoming=True, func=is_voice_1on1)` | `NewMessage(outgoing=True, func=is_voice_1on1)` |
| Reply author | You (userbot) | You (userbot) |
| Visible to other party? | Yes | Yes |
| UX risk | Contact might perceive as surveillance if unaware | Low — captioning your own content |
| Subtle distinction | Include direction in logs for debugging | — |

**Recommendation:** v1 handles both symmetrically. Don't diverge without user need. Log direction for debugging.

---

## UX Polish

1. **Placeholder → edit flow (D-1).** Biggest perceived-quality win on CPU inference.
2. **Plain-text replies, no parse_mode.** Avoids broken markdown bugs.
3. **Language prefix strategy.** Either always show 2-char prefix, or only show it on minority language. Pick one, document.
4. **Duration/length badges on long transcripts.** `(0:47 • 112 words)` helps reader decide whether to read.
5. **Fail loudly.** On error, edit placeholder to `❌ transcription failed` rather than deleting or leaving `⏳`.
6. **Don't reply with empty transcripts.** Edit placeholder to `(silence)` or delete.
7. **Reply chain respect.** Your reply threads to the voice note via `reply_to=voice_message_id`; Telegram handles it natively.
8. **Don't edit more than once.** Telegram rate-limits edits; do one edit at end. Streaming is anti-feature for CPU whisper.

---

## MVP Recommendation

**Must-ship (v1 isn't v1 without these):**
- TS-1, TS-2, TS-3 — core listen/transcribe/reply loop
- TS-4, D-8 — async queue + warm model
- TS-5, TS-6, TS-9 — session + reauth + systemd
- TS-7, TS-15 — empty/short and too-long audio guards
- TS-8 — RU/EN auto-detect
- TS-10, TS-11 — FloodWait and reconnect
- TS-13, TS-14 — cleanup + config
- D-1 placeholder+edit, D-13 markdown-safe, D-18 graceful shutdown

**Should-ship (cheap, high value):**
- D-3 retry, D-9 VAD, D-10 int8 compute, D-12 long-transcript split, D-19 skip old messages, D-21 hashed-chat-id logs, D-20 model pre-download

**Nice-to-have:**
- D-2 language tag, D-4 timestamps (long only), D-14 deleted-message handling

**Hard defer:** everything in "Deferred to Future Milestones" + all anti-features.

---

## Open Questions / [VERIFY]

- Exact list of faster-whisper hallucination strings in current builds
- Recommended `compute_type` for faster-whisper small on Oracle Ampere A1 (ARM64) vs x86 E4/E5
- Current VAD API parameter names in faster-whisper
- Current Telegram userbot ban-risk heuristics in 2025-2026
- Telethon vs Pyrogram current maintenance status

---

## Sources

Based on domain knowledge of Telethon userbot patterns, faster-whisper documented behavior, and Telegram bot UX conventions. Flagged [VERIFY] items warrant web verification before freezing requirements.

---
*Feature research for: personal Telegram voice-note transcription userbot*
*Researched: 2026-05-12*
