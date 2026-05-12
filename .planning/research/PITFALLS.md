# Pitfalls Research

**Domain:** Telegram userbot + faster-whisper (Oracle VPS)
**Researched:** 2026-05-12
**Confidence:** MEDIUM-HIGH

---

## Account Ban Risk (userbot-specific — dedicated top section)

Userbots automate a real account. Telegram's anti-spam systems apply; bans range from 24h flood to permanent.

| Pitfall | Warning signs | Prevention | Phase |
|---|---|---|---|
| **Ignoring `FloodWaitError` and hammering retries** | Repeated `FLOOD_WAIT_X` in logs, increasing X values, sudden `FloodWait` on benign ops | Catch `FloodWaitError`; sleep `e.seconds`. Let Telethon's built-in `flood_sleep_threshold` handle short waits; for longer, sleep and continue. Never retry aggressively. | P5 (hardening) |
| **Multiple concurrent sessions of the same account** | `AUTH_KEY_DUPLICATED`, random disconnects, account temp-locked | Single systemd unit per account. Session file on one host. Pre-start hook confirms no stale process: `ExecStartPre=/bin/bash -c 'pgrep -u tgbot python && exit 1 \|\| exit 0'`. | P6 (deployment) |
| **Edit-spam for progress indicators** | `FLOOD_WAIT` on `editMessage`, Telegram drops edits silently | One edit per message max. Placeholder → final edit. Never stream partial transcripts. | P4 (wiring), P5 (hardening) |
| **Fast consecutive replies in same chat** | FloodWait after 3-5 messages in a few seconds | Per-chat minimum 2s gap between replies. Queue handles this naturally with single worker. | P4, P5 |
| **First auth from datacenter IP with no prior session** | Immediate account suspension on first login | Do FIRST interactive login from your home IP (local machine), then transfer `.session` to VPS via secure channel. Never do initial login from VPS. | P1 (bootstrap) |
| **Rapid reconnects / reconnect loops** | `Restart=always` + crash loop bombards Telegram with `auth.connect` calls | `Restart=on-failure` with `StartLimitIntervalSec=300 StartLimitBurst=5`. Exit non-zero on auth errors to break the loop. | P6 |
| **Read-receipt automation** | Telegram flags "always online, always reading" patterns | Never call `ReadHistoryRequest` explicitly. Let default Telethon behavior apply. | P4 |
| **Joining/leaving groups programmatically** | Instant flag from anti-spam system | v1 doesn't join any groups — enforce via assert on handler: raise if `event.is_group or event.is_channel`. | P4 |

**Key rule:** the first successful login must come from the user's usual IP (residential / known). After that, moving the `.session` to the VPS is safe.

---

## Session & Secrets

| Pitfall | Warning signs | Prevention | Phase |
|---|---|---|---|
| **Session file world-readable** | `ls -l userbot.session` shows `644` or `666` | `chmod 600`, `chown tgbot:tgbot`. Set `umask 077` in startup script. | P1, P6 |
| **Session committed to git** | `.session` in `git log --all` | `.gitignore` excludes `*.session*`. Path outside repo (`/var/lib/tg-voice-transcriber/`). Pre-commit hook to scan. | P1 |
| **Session on shared VPS snapshot** | Oracle console shows backup snapshots containing the session | Exclude `/var/lib/tg-voice-transcriber/` from snapshot. Encrypt backup if needed. | P6 |
| **API creds in code or logs** | Grep finds `API_ID=` or `api_hash` in `git log` or `journalctl` | Load from env only. `EnvironmentFile=` in unit. `.env` gitignored. Never log `cfg.api_hash`. | P1, P6 |
| **Lost session on redeploy** | `git pull && systemctl restart` triggers first-login prompt | Session lives in `/var/lib/` (not `/opt/`); deploys touch `/opt/` only. Documented in README. | P6 |
| **Session theft = account takeover** | Attacker has full read/write of your Telegram account | File perms above + VPS hardening (SSH keys only, fail2ban, minimal attack surface). Consider dedicated account for userbot if paranoid. | P6 |

---

## Audio Pipeline Gotchas

| Pitfall | Warning signs | Prevention | Phase |
|---|---|---|---|
| **Missing ffmpeg on Ubuntu Minimal** | First voice note → `FileNotFoundError: ffmpeg`; service crashes | `apt install -y ffmpeg` in setup. Startup check: run `ffmpeg -version`, exit non-zero if missing. | P2, P6 |
| **OGG/Opus sample rate mismatch** | Whisper transcribes garbage or empty; distorted timing | Always resample to 16 kHz mono with `-ac 1 -ar 16000 -f s16le`. Don't feed raw Opus. | P2 |
| **Piping vs temp file confusion** | Deadlock when subprocess stdout fills pipe buffer while stdin not yet finished | Use `subprocess.run` with `input=bytes, capture_output=True` for small audio (<1 MB typical). For large, use async subprocess with separate read/write tasks. | P2 |
| **Zero-length audio** | ffmpeg returns empty output, whisper raises `ValueError` on empty input | Check downloaded bytes > 0 before ffmpeg. Check ffmpeg output > threshold (e.g. 1024 bytes for real audio). | P2 |
| **Corrupted OGG (partial download)** | ffmpeg stderr shows `Invalid data found when processing input` | Re-download once. If fails again, skip with log entry. | P2, P5 |
| **Temp file disk leak** | `/tmp` fills up over weeks; `df /tmp` shows growth | Use `tempfile.NamedTemporaryFile` with `delete=True`. Or in-memory bytes throughout (preferred for <1 MB). | P2 |
| **Encoding corrupt → whisper hallucinates** | Transcript is "Thanks for watching!" or "спасибо за просмотр" | Whisper hallucinations on silence/noise. Combine with VAD + duration check. See language detection section. | P3, P5 |

---

## faster-whisper on CPU Gotchas

| Pitfall | Warning signs | Prevention | Phase |
|---|---|---|---|
| **`compute_type="float16"` on CPU** | RuntimeError "float16 is not supported on CPU" | Use `compute_type="int8"` on CPU. Never `float16`. Explicit check at startup. | P3 |
| **RAM explosion on long audio** | Process OOM-killed, `dmesg` shows OOM event | `WhisperModel(...).transcribe(audio, vad_filter=True)` — VAD trims silence. Hard duration cap (10 min). Use `condition_on_previous_text=False` to reduce KV cache growth. | P3, P5 |
| **Thread oversubscription** | CPU at 100% but transcription slower than serial | Set `cpu_threads=` to physical core count, not SMT count. For Oracle Ampere A1 (4 OCPU = 4 cores), use 4. Set `num_workers=1` explicitly. | P3 |
| **First-run model download under systemd** | `systemctl start` times out on first boot (~2 min download) | Pre-download model in setup script: `python -c "from faster_whisper import WhisperModel; WhisperModel('small')"`. Or `TimeoutStartSec=600` on unit. | P6 |
| **Model path / cache permission** | `PermissionError` when loading model, or model redownloaded every start | Set `HF_HOME=/var/lib/tg-voice-transcriber/.cache` or `download_root=...`. Owned by `tgbot`. | P3, P6 |
| **Wrong multilingual variant** | English-only variant can't detect Russian | Use multilingual `small` (default), not `small.en`. | P3 |
| **E2.1.Micro OOM (Oracle free 1GB shape)** | OOM-killer fires, journal shows `Killed` | Use Ampere A1 free shape (4 OCPU / 24 GB) instead. E2.1.Micro cannot run `small` + Telethon comfortably. | P6 (shape selection) |
| **ctranslate2 ARM wheels** | `pip install` works on laptop but fails on Ampere A1 | Explicit `pip install ctranslate2==X.Y.Z --index-url https://pypi.org/simple/` with known-good version. Confirm aarch64 wheel exists for that version before deploy. | P6 |

---

## Async / Blocking Traps

| Pitfall | Warning signs | Prevention | Phase |
|---|---|---|---|
| **Calling `model.transcribe()` from event loop** | Bot stops responding during transcription; Telegram drops keepalive; reconnect storms | `await loop.run_in_executor(pool, model.transcribe, audio)`. NEVER direct call. Single-thread executor. | P3, P4 |
| **Using `subprocess.run` directly from async handler** | Same freeze as above | `asyncio.create_subprocess_exec(...)` in async code. Or run ffmpeg in executor. | P2 |
| **`time.sleep` instead of `asyncio.sleep`** | Entire event loop pauses | Always `await asyncio.sleep(...)` in async context. | P5 |
| **Unbounded task creation** | Memory grows with message rate | Use single long-lived worker coroutine, not `asyncio.create_task()` per message. | P4 |
| **Shared state without lock** | Race conditions on queue, config, counters | asyncio single-threaded so most is safe; but the executor thread CAN race with main loop on shared dicts. Keep whisper thread stateless. | P3 |
| **Not cancelling tasks on shutdown** | `asyncio.CancelledError` eaten; tasks orphaned | On SIGTERM, cancel worker task, await drain, then `client.disconnect()`. | P5 |

---

## Privacy & Logging

| Pitfall | Warning signs | Prevention | Phase |
|---|---|---|---|
| **Logging transcript text at INFO** | `journalctl` contains "She said she's running late" etc. | Log metadata only: chat_id_hash, duration, transcript_chars, language. Transcript text only at DEBUG behind `LOG_TRANSCRIPTS=true` (off by default). | P5 |
| **Raw chat_id in logs** | Anyone with journald access can reverse-map to your contacts | Hash with salted SHA-256. Salt in env, not logs. | P5 |
| **World-readable journal** | Non-root users on VPS can `journalctl -u tg-voice-transcriber` | Ubuntu default: only `systemd-journal` group reads. Confirm `adm` group membership is minimal. | P6 |
| **Log rotation not configured** | `journalctl` size grows unbounded | systemd-journald has default rotation; confirm `/etc/systemd/journald.conf` has `SystemMaxUse=500M` or similar. | P6 |
| **Backup tarballs of `/var/log`** | Backups carry sensitive log data off-box | Exclude logs from backup, or encrypt backups. | P6 |
| **Committing `.env` with real creds** | `git log` shows API_HASH value | Pre-commit hook: `git secrets` or manual grep. `.env` in `.gitignore`. Start with `.env.example`. | P1 |

---

## Deployment & systemd

| Pitfall | Warning signs | Prevention | Phase |
|---|---|---|---|
| **Wrong `User=` (running as root)** | `ls -l userbot.session` shows `root:root` | `User=tgbot` in unit. Create service user with `adduser --system --group tgbot`. | P6 |
| **`WorkingDirectory=` missing** | venv paths break; `ModuleNotFoundError` | Set `WorkingDirectory=/opt/tg-voice-transcriber`. | P6 |
| **`EnvironmentFile=` missing quotes handling** | systemd truncates values at `=` | Use one `KEY=VALUE` per line, no shell quoting. No spaces around `=`. | P6 |
| **`Restart=always` + `StartLimitBurst=` not set** | Auth failures → infinite restart loop → ban | `Restart=on-failure`, `StartLimitIntervalSec=300`, `StartLimitBurst=5`. Exit code for unrecoverable errors = 0 or >128. | P6 |
| **SELinux/AppArmor on Oracle image** | Process can't read session file despite perms | Oracle Ubuntu default has AppArmor. Confirm no profile for python. If systemd hardening (`ProtectSystem=strict`) blocks something, add `ReadWritePaths=/var/lib/tg-voice-transcriber`. | P6 |
| **`MemoryDenyWriteExecute=yes` breaks CTranslate2** | `Illegal instruction` or mysterious crash | CTranslate2 may use JIT. Set `MemoryDenyWriteExecute=no` if issues. | P6 |
| **Timezone mismatch affecting timestamps** | Log timestamps look wrong; "skip old message" logic fires incorrectly | Set VPS TZ to UTC: `timedatectl set-timezone UTC`. All comparisons in UTC. | P6 |
| **`After=network.target` not `network-online.target`** | Service starts before network ready, fails to connect, then restart-storms | Use `After=network-online.target` with `Wants=network-online.target`. | P6 |

---

## Language Detection Edge Cases

| Pitfall | Warning signs | Prevention | Phase |
|---|---|---|---|
| **Whisper language detection on <3s audio** | Random language picks (Chinese for "hello", etc.) | Clamp to `{ru, en}` via `language_detection_threshold=0.5` AND explicit check. If confidence low or not in set, default to user's primary (`ru` in this case). | P3 |
| **Code-switching within one voice note** | User speaks Russian with English tech terms; whisper picks one and transliterates the other | Known limitation. Document in README. If problematic, consider running detection per segment (`language_detection_segments=3`) — slower. | P3 |
| **Numbers, acronyms, proper nouns** | "GPT" → "Джи Пи Ти", "HR" → "Эйч Ар" in Russian | Expected whisper behavior. Don't try to fix at transcription layer. | — |
| **Loud music / background noise** | Transcript is "♪ Music ♪" or hallucinated lyrics | VAD helps. Consider a noise filter pre-step for chronic cases (defer to v2). | P3 |

---

## Cost Creep

User wants it FREE. Silent paths to paid:

| Pitfall | Warning signs | Prevention | Phase |
|---|---|---|---|
| **Wrong Oracle shape (1 GB E2.1.Micro) OOMs → user upgrades to paid** | OOM events, user asks "why is this crashing" | Use Ampere A1 free shape (4 OCPU / 24 GB). Document this explicitly. | P6 |
| **Model cache fills VPS disk** | `df -h` shows `/var/lib/tg-voice-transcriber/.cache` growing | Pin exactly one model (`small`). Don't let users swap models at runtime (would cache multiple). | P3, P6 |
| **Audio temp files leak** | `/tmp` fills or home dir fills | In-memory processing. Assert cleanup in tests. | P2 |
| **Journald growing without rotation** | `/var/log/journal` multi-GB | Default rotation is sufficient; verify `SystemMaxUse`. | P6 |
| **Bandwidth on model download** | First boot downloads ~500 MB on metered network | Pre-download in setup step on a wired connection. | P6 |
| **Silently switching to cloud API for "just this one"** | Code sneaks in OpenAI SDK | Not a technical pitfall; explicit anti-feature in PROJECT.md. | — |

---

## Phase-Specific Warnings Map

| Phase | Top pitfalls to watch |
|---|---|
| **P1 (bootstrap, session)** | First-login IP, session perms, creds in git, `.env.example` vs `.env` |
| **P2 (audio pipeline)** | Missing ffmpeg, sample rate, zero-length audio, temp file leak |
| **P3 (transcriber)** | `float16` on CPU, model cache path, language clamping, multilingual variant, thread count |
| **P4 (event wiring)** | Blocking in handler, FIFO ordering, group filter assertion, read-receipt automation |
| **P5 (hardening)** | FloodWait handling, graceful shutdown, log privacy, correlation IDs, message-length split |
| **P6 (deployment)** | Shape selection, systemd restart caps, ARM wheels, timezone, network-online.target |

---

## Open Questions / [VERIFY]

- Current exact `language_detection_threshold` param syntax in installed faster-whisper version
- ARM64 wheel availability for `ctranslate2` + `faster-whisper` at time of deployment
- Oracle Cloud current default Ubuntu image (22.04 vs 24.04, whether ffmpeg is in default repos)
- Current Telegram userbot flood-wait heuristics (re-check before locking ban-risk logic)
- Exact `MemoryDenyWriteExecute` compatibility with CTranslate2 JIT paths

---

## Sources

- Telethon docs — session, FloodWait, events. https://docs.telethon.dev/ (HIGH)
- faster-whisper GitHub — compute_type matrix, VAD, language detection. https://github.com/SYSTRAN/faster-whisper (HIGH)
- systemd.exec + systemd.service man pages — hardening directives. https://www.freedesktop.org/software/systemd/man/ (HIGH)
- Oracle Cloud Free Tier shape specs. https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm (HIGH, stable)
- FHS 3.0 for path conventions. https://refspecs.linuxfoundation.org/FHS_3.0/ (HIGH)
- Community post-mortems on userbot bans (various Telegram dev groups, 2023-2024) (MEDIUM — synthesized)

---
*Pitfalls research for: personal Telegram voice-note transcription userbot*
*Researched: 2026-05-12*
