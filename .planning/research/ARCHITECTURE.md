# Architecture Patterns

**Domain:** Personal Telegram userbot (self-hosted, single-user, voice-note → text)
**Researched:** 2026-05-12
**Overall confidence:** HIGH

## System Architecture

Single Python process, single asyncio event loop, one background worker task, Whisper inference offloaded to a dedicated thread so it never blocks the loop.

```
                  ┌─────────────────────────────────────────────────────┐
                  │                 systemd (tg-voice.service)          │
                  │  User: tgbot   WorkingDir: /opt/tg-voice-transcriber│
                  └─────────────────────────────────────────────────────┘
                                          │
                                          ▼
 ┌───────────────────────────────────────────────────────────────────────────┐
 │  Python process  (single asyncio event loop)                              │
 │                                                                           │
 │   Telegram MTProto                                                        │
 │         │                                                                 │
 │         ▼                                                                 │
 │   ┌──────────────┐    NewMessage(voice)    ┌──────────────┐               │
 │   │  Telethon    │────────────────────────▶│ EventHandler │               │
 │   │  Client      │                         │  (filter +   │               │
 │   │  (session)   │◀────reply text──────────│  enqueue)    │               │
 │   └──────────────┘                         └──────┬───────┘               │
 │         ▲                                         │                       │
 │         │                                         ▼                       │
 │         │                                 ┌───────────────┐               │
 │         │                                 │ asyncio.Queue │  maxsize=N    │
 │         │                                 │  (Job items)  │  (backpressure│
 │         │                                 └───────┬───────┘   via         │
 │         │                                         │           put_nowait) │
 │         │                                         ▼                       │
 │         │                                 ┌───────────────┐               │
 │         │                                 │ Worker task   │  (awaits      │
 │         │                                 │  (1 consumer) │   queue.get)  │
 │         │                                 └───────┬───────┘               │
 │         │                                         │                       │
 │         │                ┌────────────────────────┼────────────────────┐  │
 │         │                ▼                        ▼                    ▼  │
 │         │        ┌──────────────┐         ┌──────────────┐     ┌───────────────┐
 │         │        │AudioPipeline │         │  Transcriber │     │   Formatter   │
 │         │        │ download .ogg│──wav──▶ │faster-whisper│──▶ │ strip/compose │
 │         │        │ → FFmpeg     │  bytes  │run_in_executor│    │  reply text   │
 │         │        │ → 16k mono   │  or     │ (thread pool)│    └──────┬────────┘
 │         │        │   s16le WAV  │  tmp    └──────────────┘            │
 │         │        └──────────────┘          (GIL released in           │
 │         │                                   CTranslate2 native)       │
 │         │                                                              │
 │         └───────────────── ReplyService (client.send_message) ◀────────┘
 │                                                                           │
 └───────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
                       stdout/stderr → journalctl (structured logs)
```

### Data flow

1. Telethon receives `NewMessage` update and dispatches to the handler.
2. `EventHandler` filters: 1-on-1 chat, `message.voice`, incoming or outgoing per config.
3. Handler builds `Job(chat_id, message_id, sender_id, direction)` and calls `queue.put_nowait(job)`. If full, logs drop.
4. Worker `await queue.get()` → downloads voice via `client.download_media(..., file=bytes)` (in memory, voice notes are small).
5. `AudioPipeline` pipes bytes through FFmpeg (`ffmpeg -i pipe:0 -ac 1 -ar 16000 -f s16le pipe:1`) producing 16 kHz mono PCM.
6. `Transcriber.transcribe()` calls `await loop.run_in_executor(whisper_executor, model.transcribe, audio, **opts)`. Single-thread `ThreadPoolExecutor` serializes Whisper calls.
7. `Formatter` joins segments, trims, optionally prepends header.
8. `ReplyService` calls `client.send_message(chat_id, text, reply_to=message_id)`. Exceptions (FloodWait, RPC) retried here.
9. Worker calls `queue.task_done()` and loops.

## Components

| Component | Responsibility | Communicates with | Owns state |
|---|---|---|---|
| `TelegramClient` (Telethon wrapper) | MTProto connection, session persistence, send/download | EventHandler (in), ReplyService + AudioPipeline (out) | `.session` SQLite file |
| `EventHandler` | Subscribe to `NewMessage`; filter; enqueue `Job`; respond to queue-full | Queue | none |
| `Queue` (`asyncio.Queue`) | Decouple arrival from transcription rate; bound memory | Handler (producer), Worker (consumer) | in-memory job list |
| `Worker` (coroutine) | Pull jobs one at a time; orchestrate pipeline stages; per-job error handling | AudioPipeline, Transcriber, Formatter, ReplyService | current job only |
| `AudioPipeline` | Download voice bytes; invoke FFmpeg subprocess to produce 16 kHz mono PCM | TelegramClient, FFmpeg | temp buffers |
| `Transcriber` | Load faster-whisper once at boot; run `model.transcribe()` in thread; return segments | Worker | loaded model (heap, ~500 MB for small int8) |
| `Formatter` | Segments → final reply string; language tag; 4096-char cap | Worker | none |
| `ReplyService` | Send reply with `reply_to`; handle `FloodWaitError`; retry transient errors | TelegramClient | none |
| `Config` | Load env + validate; typed settings | All | env-backed |
| `Logging` | Structured stdout logs with correlation ID; privacy scrubber | All | none |

**Component boundary rule:** `EventHandler` must not await anything expensive. Everything post-enqueue lives in worker's call chain.

## Concurrency Model

**Chosen: single process, single async loop, 1 worker coroutine, 1-thread executor for Whisper.**

### Why not multi-process
- faster-whisper / CTranslate2 releases the GIL during inference, so a thread suffices.
- ProcessPoolExecutor would duplicate the ~500 MB model per worker. On small Oracle VPS, nonstarter.
- Two-process design (event + worker via Redis/RQ) adds IPC, supervision, second systemd unit for zero throughput benefit on single CPU-bound model.

### Why a worker task, not transcribe inline
- Telethon dispatches handlers concurrently on the same loop. Inline means 5 simultaneous voice notes each await Whisper — memory unbounded, ordering chaotic.
- Single worker + queue gives FIFO, bounded memory, single place to measure latency.

### Why one worker, not N
- Whisper-small on one CPU already saturates cores (CTranslate2 uses all CPUs per request). N workers interleave CPU time; throughput flat, latency doubled.

### Backpressure
- `queue.maxsize = 10` (configurable). On `QueueFull`: log drop. Optionally reply "⏳ still transcribing earlier, skipped" (off by default to avoid noise).
- Expose queue depth in periodic log lines (`queue_depth=3`) for tuning.

### Why `run_in_executor` for Whisper
- `model.transcribe()` is sync. Direct call from worker coroutine blocks event loop — Telethon can't receive updates or send reply.
- Dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")` avoids contention with other incidental `run_in_executor` calls.

## Audio Pipeline: Failure Points & Retries

| Stage | Failure mode | Handling | Retry? |
|---|---|---|---|
| Download voice | FloodWait, network blip, message deleted | Catch `FloodWaitError` → sleep; catch RPC → 1 retry with 2s delay | 1 retry, then drop |
| FFmpeg spawn | Binary missing (ENOENT) | Fail-fast at startup (`ffmpeg -version` health check) | No |
| FFmpeg transcode | Malformed OGG, unexpected codec | Capture stderr, log, skip | No (data problem) |
| Whisper inference | Model load fail at boot; runtime crash | Boot: fail-fast. Runtime: catch, log, react ❌ | No per-request |
| Send reply | FloodWait, message-too-long, chat-gone | FloodWait sleep+retry; >4096 chars → split; gone → drop | Up to 3 for transient |

**Rule:** retry network-facing ops, never retry deterministic compute.

## Configuration & Secrets

**Strategy: `.env` file, loaded via `pydantic-settings`, kept outside code tree.**

```
/etc/tg-voice-transcriber/env       # root:tgbot 0640, secrets
/opt/tg-voice-transcriber/          # tgbot:tgbot, code tree
/var/lib/tg-voice-transcriber/      # tgbot:tgbot 0700, session file
```

- systemd unit `EnvironmentFile=/etc/tg-voice-transcriber/env` loads `API_ID`, `API_HASH`, `PHONE_NUMBER`, `TG_SESSION_NAME`, `WHISPER_MODEL`, `WHISPER_COMPUTE_TYPE`, `QUEUE_MAXSIZE`, `DIRECTIONS`, `LOG_LEVEL`.
- `Config` (pydantic `BaseSettings`) reads env, validates types, exposes typed singleton.
- No Vault. `env` file `0640 root:tgbot`. `.env.example` committed; `.env` gitignored.
- Back up `/etc/tg-voice-transcriber/env` + session file together (useless apart).

### Session file
- Location: `/var/lib/tg-voice-transcriber/<session_name>.session`.
- Perms: `0600`, owner `tgbot:tgbot`.
- Backup: copy while service **stopped** (SQLite-safe). Store encrypted off-box.
- Re-auth: on `AuthKeyError` at startup, log `AUTH_REQUIRED`, exit non-zero. Operator runs `scripts/login.py` then `systemctl restart`.

## Observability

**Sink: stdout/stderr → journald via `journalctl -u tg-voice-transcriber`.**

- Format: key=value or JSON lines. Timestamp added by journald.
- Levels: `DEBUG` (bytes, ffmpeg args) / `INFO` (job accepted, completed) / `WARNING` (queue full, FloodWait, retries) / `ERROR` (download failed, whisper crash, send failed).
- **Privacy:** do NOT log transcript content at `INFO`. Log `chat_id_hash`, `msg_id`, `voice_duration_s`, `audio_bytes`, `whisper_latency_ms`, `language_detected`, `transcript_chars`. Transcript text only at `DEBUG` behind `LOG_TRANSCRIPTS=true` flag (off by default). Hash `chat_id`/`sender_id` with salt.
- One correlation ID (ULID) per job across all log lines.
- Per-job timing breakdown (download / ffmpeg / whisper / send) is enough to spot regressions.

## VPS Deployment Layout

Target: Ubuntu 22.04/24.04 on Oracle Cloud VPS.

```
/opt/tg-voice-transcriber/                  # 0755 tgbot:tgbot (code)
├── .venv/
├── src/tg_voice_transcriber/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── client.py                            # Telethon wrapper
│   ├── handlers.py
│   ├── queue.py
│   ├── worker.py
│   ├── audio.py                             # AudioPipeline
│   ├── transcriber.py
│   ├── formatter.py
│   ├── reply.py
│   └── logging.py
├── scripts/
│   ├── login.py                             # one-shot interactive re-auth
│   └── smoke.py                             # decode local .ogg → text
├── pyproject.toml
└── README.md

/etc/tg-voice-transcriber/env                # 0640 root:tgbot
/var/lib/tg-voice-transcriber/userbot.session  # 0600 tgbot:tgbot
/etc/systemd/system/tg-voice-transcriber.service  # 0644 root:root
```

### Service user

```
adduser --system --group --home /var/lib/tg-voice-transcriber --shell /usr/sbin/nologin tgbot
```

### systemd unit shape

```ini
[Unit]
Description=Telegram voice-note transcriber
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tgbot
Group=tgbot
WorkingDirectory=/opt/tg-voice-transcriber
EnvironmentFile=/etc/tg-voice-transcriber/env
ExecStart=/opt/tg-voice-transcriber/.venv/bin/python -m tg_voice_transcriber
Restart=on-failure
RestartSec=5s
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/tg-voice-transcriber
MemoryMax=1G
CPUQuota=200%

[Install]
WantedBy=multi-user.target
```

**Why `/opt` over `/home/ubuntu`:** FHS-correct location for self-contained service software; decouples from human account.

## Patterns to Follow

### Enqueue-and-return event handlers
Handlers filter + enqueue, return in milliseconds.
```python
@client.on(events.NewMessage(incoming=True, outgoing=True))
async def on_message(event):
    if not event.is_private or not event.message.voice:
        return
    job = Job(chat_id=event.chat_id, msg_id=event.id, sender_id=event.sender_id)
    try:
        queue.put_nowait(job)
    except asyncio.QueueFull:
        log.warning("queue_full", extra={"chat": hash_id(event.chat_id)})
```

### Blocking work via `run_in_executor`
```python
class Transcriber:
    def __init__(self, model):
        self._model = model
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
    async def transcribe(self, pcm: bytes) -> TranscriptResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._sync_transcribe, pcm)
```

### Fail-fast startup
`main()` verifies FFmpeg on PATH, model loads, session readable, Telethon connects. Exit non-zero on failure.

## Anti-Patterns

| Anti-pattern | Why bad | Instead |
|---|---|---|
| Transcribing inside the handler | Blocks event loop; memory blows up on parallel voice notes; kills FIFO | Queue + single worker |
| Writing audio to disk between stages | Voice notes <1 MB; adds I/O, cleanup, permission surface | Keep in memory, pipe FFmpeg via stdin/stdout |
| Logging transcripts at INFO | Privacy disaster — journald in backups, log shippers | Metadata only; content gated behind debug flag |
| Loading Whisper model per request | 2-5s load, ~500 MB allocation, memory fragmentation | Load once in `Transcriber.__init__` |
| Unbounded queue | Burst can OOM | `maxsize=10` with explicit drop |
| Session file in code tree | `git add .` accidents, perm drift, wiped on redeploy | `/var/lib/tg-voice-transcriber/` owned by service user |

## Suggested Build Order (STANDARD granularity → 6 phases)

1. **Phase 1 — Project bootstrap & Telethon session.** Repo layout, `pyproject.toml`, `Config`, `TelegramClient` wrapper, `scripts/login.py`. Deliverable: log in once, print "connected as @me".

2. **Phase 2 — Audio pipeline.** `AudioPipeline` component. Local `.ogg` → 16 kHz mono PCM via FFmpeg. Unit-tested. No Telegram dep. Deliverable: `scripts/smoke.py fixture.ogg` prints byte count.

3. **Phase 3 — Transcription engine.** `Transcriber` with faster-whisper small int8 CPU, loaded once, `run_in_executor`. RU + EN auto-detect verified. Deliverable: smoke prints transcript of RU and EN fixtures.

4. **Phase 4 — End-to-end event wiring.** `EventHandler` + queue + `Worker` + `Formatter` + `ReplyService`. Running locally transcribes real voice notes and replies. Deliverable: working bot on dev machine.

5. **Phase 5 — Hardening.** Bounded queue + drop policy, per-stage error handling, FloodWait retry, message-too-long splitting, structured logging with privacy scrubber, correlation IDs, fail-fast startup, graceful shutdown on SIGTERM. Deliverable: survives burst of 10 voice notes + network blip.

6. **Phase 6 — VPS deployment.** Service user, filesystem layout, venv install, systemd unit with hardening, journalctl verified, backup procedure documented. Deliverable: `systemctl status tg-voice-transcriber` green on Oracle VPS, surviving reboot.

**Parallel pairs:** Phase 2 + Phase 3 (once interfaces agreed). Phase 5 fan-out.
**Sequential:** 1 → 4, 2+3 → 4, 4 → 5, 5 → 6.

## Sources

- Telethon official docs — events, session files, `download_media`, `send_message`, FloodWait. https://docs.telethon.dev/ (HIGH)
- faster-whisper README + CTranslate2 notes — GIL release during inference, CPU compute types. https://github.com/SYSTRAN/faster-whisper (HIGH)
- Python asyncio reference. https://docs.python.org/3/library/asyncio-queue.html (HIGH)
- systemd.exec hardening. https://www.freedesktop.org/software/systemd/man/systemd.exec.html (HIGH)
- FHS for `/opt`. https://refspecs.linuxfoundation.org/FHS_3.0/ (HIGH)
- pydantic-settings docs. https://docs.pydantic.dev/ (HIGH)
- FFmpeg piping. https://ffmpeg.org/ffmpeg.html (HIGH)

---
*Architecture research for: personal Telegram voice-note transcription userbot*
*Researched: 2026-05-12*
