# Stack Research

**Domain:** Telegram userbot + local voice transcription on Ubuntu VPS
**Researched:** 2026-05-12
**Confidence:** HIGH for core packages, MEDIUM for exact pin versions (pin against latest stable at setup time)

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.11 (3.10+ OK) | Runtime | 3.11 is the sweet spot: mature async, good perf, universal wheel support. Avoid 3.13 — some ML wheels lag. |
| Telethon | 1.43.x (1.x series) | MTProto userbot client | Mature, widely used, good async/event model, stable session format. **Use 1.x, not the 2.0 alpha.** 2.0 has breaking API changes and isn't stable yet as of May 2026. |
| faster-whisper | 1.2.x | Whisper inference wrapper | CTranslate2-backed, 2-4× faster than reference Whisper at equal accuracy, much lower RAM. Active maintenance. |
| CTranslate2 | 4.7.x | Inference engine (faster-whisper dep) | Transformer inference engine with int8 quantization, CPU-optimized (Intel MKL, oneDNN, Ruy for ARM). Supports both x86-64 and AArch64. Installed as a transitive dep of faster-whisper. |
| FFmpeg | 4.x or 6.x (system package) | OGG/Opus → PCM conversion | Universal audio tool, Telegram voice notes are Opus-in-OGG, Whisper wants 16 kHz mono PCM. Install via apt, not pip. |
| systemd | (system, Ubuntu default) | Process supervisor | Auto-restart, journald logging, resource limits, standard Ubuntu tooling. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | 2.x | Data validation | Via pydantic-settings for typed config |
| pydantic-settings | 2.x | Env-driven config | Load API_ID/API_HASH/paths from env with type checking |
| python-dotenv | 1.x | `.env` loader (dev only) | Local dev convenience; production uses systemd `EnvironmentFile=` |
| structlog | 24.x or 25.x | Structured logging | Key-value log lines, privacy-scrubbing processors, correlation IDs |
| tenacity | 9.x | Retry decorators | Retry download/reply on transient errors |
| uvloop | 0.21.x | Faster asyncio event loop | Optional; drop-in speedup on Linux. Not ARM-safe on all kernels — test before enabling |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| ruff | Linter + formatter | Replaces black + flake8 + isort; fast, one tool |
| pytest | Test runner | For audio pipeline unit tests and integration smoke |
| pytest-asyncio | Async test support | For testing worker coroutines |

## Installation

### System packages (Ubuntu 22.04 / 24.04)

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip ffmpeg git build-essential
# Verify: ffmpeg -version && python3.11 --version
```

### Python packages (inside venv)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel

# Core
pip install \
  "telethon>=1.43,<2.0" \
  "faster-whisper>=1.2,<2.0" \
  "pydantic>=2.6,<3.0" \
  "pydantic-settings>=2.2,<3.0" \
  "structlog>=24.1,<26.0" \
  "tenacity>=9.0,<10.0"

# Dev (optional)
pip install -D "ruff>=0.5" "pytest>=8.0" "pytest-asyncio>=0.23"
```

**ARM64 (Oracle Ampere A1) note:** CTranslate2 4.7+ has prebuilt `aarch64` wheels on PyPI. Plain `pip install faster-whisper` pulls the correct wheel automatically. If it falls back to source build, something is off — verify with `pip install --only-binary=:all: ctranslate2`.

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Telethon | Pyrogram | Similar capabilities. Pick Telethon because it's been continuously maintained; Pyrogram had a leadership transition and forks. Community momentum favors Telethon in 2026. |
| Telethon | python-telegram-bot | PTB is bot-API only (can't run as a userbot). Use for non-userbot projects. |
| faster-whisper | openai-whisper (reference) | 2-4× slower on CPU, higher RAM. Use only if you need exact reference behavior for research. |
| faster-whisper | whisperX | Adds forced alignment + diarization. Use when you need word-level timestamps or speaker labels — out of scope for v1. |
| faster-whisper (small) | faster-whisper (medium / large-v3) | Medium needs ~5 GB RAM, large-v3 ~10 GB and is slow on CPU. Upgrade only if small accuracy is insufficient. |
| faster-whisper | vosk | Vosk is lighter but quality is noticeably lower on conversational audio. Use if RAM-constrained below ~1 GB. |
| faster-whisper (local) | Groq API / OpenAI API | Cloud APIs are fast but cost money and send audio off-box. Rejected for v1 (privacy + free-forever requirement). |
| structlog | stdlib logging | stdlib works but lacks structured output, processors, and context binding. structlog is cheap to add day one. |
| pydantic-settings | os.environ + argparse | Works for small configs; pydantic-settings gives typed validation and a single schema source. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Telethon 2.0.0a* | Alpha, breaking API changes mid-flight, docs unstable | Telethon 1.43.x |
| `compute_type="float16"` on CPU | Not supported on CPU, runtime error | `compute_type="int8"` (or `int8_float32` if accuracy matters more than speed) |
| `whisper` (OpenAI reference) on CPU | Too slow, too RAM-hungry for this VPS | `faster-whisper` |
| `faster-whisper` `small.en` variant | English-only; can't transcribe Russian | multilingual `small` (default when you omit `.en`) |
| Running whisper via `subprocess.run(["whisper", ...])` | CLI overhead, extra process per call, hard to tune | Import `faster_whisper.WhisperModel` directly, load once |
| Storing `.session` in repo | `git add .` accidents leak full account access | `/var/lib/tg-voice-transcriber/` outside repo, `0600` perms |
| `time.sleep()` anywhere in async code | Blocks the event loop, freezes the bot | `await asyncio.sleep(...)` |
| `Restart=always` on systemd for auth-fail | Causes restart-storm → potential Telegram ban | `Restart=on-failure` + `StartLimitIntervalSec=300 StartLimitBurst=5` |
| Ubuntu Minimal without `ffmpeg` | Silent failure on first voice note | Install `ffmpeg` explicitly in setup |
| pip installing `ctranslate2` without checking ARM wheels | Source build takes 20+ min and may fail | Use `--only-binary=:all:` to force wheel; verify aarch64 wheel exists |

## Stack Patterns by Variant

**If CPU-only (this project):**
- `WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=<physical cores>)`
- Single-thread executor for whisper calls
- One queue worker

**If GPU (future, not this milestone):**
- `WhisperModel("medium", device="cuda", compute_type="float16")`
- Still one worker; GPU inference is serial per model
- Need NVIDIA runtime on VPS — different instance class, out of scope

**If Oracle Ampere A1 (ARM64):**
- `compute_type="int8"` — supported, uses Ruy backend under the hood
- `cpu_threads=4` for the free A1 Flex (4 OCPU)
- Prebuilt wheel pulls cleanly as of CTranslate2 4.7

**If Oracle x86 AMD (E2 / E4 / E5):**
- Same compute_type
- `cpu_threads` matches vCPU count
- Intel/AMD MKL path in CTranslate2 is well-exercised

**If low RAM (<2 GB) — NOT RECOMMENDED:**
- Drop to `tiny` or `base` model; quality suffers for Russian
- Really, use an A1 free shape instead

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| Telethon 1.43.x | Python 3.8 – 3.12 | 3.11 recommended. 3.13 may work but has less field testing. |
| faster-whisper 1.2.x | Python 3.9 – 3.12, CTranslate2 4.6+ | Pulls CTranslate2 automatically |
| CTranslate2 4.7.x | Python 3.9 – 3.12 on Linux x86-64 / aarch64, macOS, Windows x86-64 | ARM64 Linux wheel available |
| pydantic 2.x ↔ pydantic-settings 2.x | Must match major (both v2) | v1 is EOL for new projects |
| structlog 24+ | Python 3.8+ | Python-native, no C deps |
| uvloop (if used) | Linux + macOS only, not Windows | Skip on Windows dev |

### Python / Telethon event loop note
Telethon 1.x works with `asyncio` natively. Do NOT use the `telethon.sync` facade in a server context — it's for REPLs and scripts. Use the async API in long-running services.

### Telethon 1.x ↔ 2.x warning
Telethon 2.0 (alpha as of early 2026) reorganizes events, client API, and session handling. Do not mix 1.x tutorials with a 2.x install. Pin `telethon<2.0` until 2.x goes stable and we revisit.

## Sources

- Telethon on PyPI (v1.43 confirmed current) — https://pypi.org/project/Telethon/
- faster-whisper on PyPI (v1.2 confirmed current) — https://pypi.org/project/faster-whisper/
- CTranslate2 4.7.1 docs confirming AArch64 support — https://opennmt.net/CTranslate2/installation.html
- SYSTRAN/faster-whisper GitHub — https://github.com/SYSTRAN/faster-whisper
- CTranslate2 GitHub README re: Ruy / MKL / oneDNN backends — https://github.com/OpenNMT/CTranslate2
- Telethon docs (1.35.x structure, stable idioms) — https://docs.telethon.dev/
- pydantic-settings 2.x docs — https://docs.pydantic.dev/latest/concepts/pydantic_settings/

---
*Stack research for: Telegram voice-transcription userbot*
*Researched: 2026-05-12*
