# Phase 7 — Channel Digest — Implementation Summary

**Phase:** 7 (Milestone v1.1)
**Status:** ✅ Complete and deployed (live on VPS)
**Completed:** 2026-05-14

## What was built

LLM-filtered channel digest subsystem that runs alongside the existing voice-transcription userbot. Listens for new posts in user-selected Telegram channels, batches them, scores via Groq Llama-3.3-70B, and delivers a curated summary message to a configured private channel every 30 minutes.

## Files created

```
src/tg_voice_transcriber/
├── groq_client.py           # Shared Groq HTTP client with multi-key rotation (used by whisper + LLM)
└── digest/
    ├── __init__.py
    ├── db.py                # SQLite schema + async CRUD helpers (config, tracked_channels, post_buffer, dedupe_cache, stats)
    ├── ingest.py            # NewMessage handler — captures channel posts, dedupes, buffers
    ├── scorer.py            # DigestScorer — batched Groq LLM call returning per-post scores + summaries + deal flags
    ├── formatter.py         # Format scored posts → digest message(s); top-N or threshold mode
    ├── scheduler.py         # Background asyncio task that drains buffer every N minutes
    └── commands.py          # /digest setup/pause/now/channels/prefs/stats/threshold/top/unsub/help

tests/
├── test_digest_db.py        # 15 tests for SQLite layer
├── test_digest_scorer.py    # 6 tests for LLM scorer (mocked Groq)
└── test_digest_formatter.py # 12 tests for formatter (threshold + top-N modes)
```

Total: 72 tests passing (all green as of 2026-05-14).

## Key features delivered

| Feature | Status |
|---|---|
| Auto-add channels on first post (default-track-all) | ✅ |
| SQLite persistence at `/var/lib/tg-voice-transcriber/digest.db` | ✅ |
| Dedupe across batches (24h hash cache) | ✅ |
| Groq Llama-3.3-70B scoring with retry-on-bad-JSON | ✅ |
| Top-N mode (always deliver top N posts per cycle) | ✅ |
| Threshold mode (only score >= N) | ✅ (set top_n=0 to use) |
| Self-contained summaries with action steps | ✅ (LLM prompts for WHAT/WHERE/HOW) |
| Free-deal detection + value estimate | ✅ (`💰 ~$N free` badges) |
| Scam pattern flagging | ✅ (`⚠️ suspected scam` badges) |
| Per-post score label `[X/10]` | ✅ |
| Source links to original posts | ✅ (public + private channel formats) |
| `/digest` command surface | ✅ (10 subcommands) |
| 30-min scheduler with empty-buffer skip | ✅ |
| Multi-key rotation on 429 | ✅ (Groq) |
| Migration: `top_n` column added to existing DBs | ✅ |

## Key decisions taken during execution

- **Groq cloud over local faster-whisper.** Local whisper on the 956 MB Oracle VPS took ~2 minutes for a 1-second voice note (CPU-only, swap-thrashing). Switched to Groq's `whisper-large-v3-turbo` for sub-3-second transcription. Tradeoff: audio leaves the VPS, but session was already on a single host so privacy model is unchanged.
- **Multi-key rotation for Groq.** Built into `GroqClient` since one user provided 6 keys. Now reduced to 1 working key (others were restricted by Groq).
- **Top-N mode added late.** Original design used threshold mode only. User reported "looks broken because most cycles deliver nothing". Added `top_n` config so every cycle reliably delivers at least N posts (when buffer non-empty). Default is now 5.
- **Self-contained summaries.** First LLM prompt produced terse "see post for details" summaries. Updated prompt to require WHAT/WHERE/HOW/DEADLINE so user doesn't need to click the source link.

## Deployed state (live VPS)

- Service: `tg-voice-transcriber.service` on 158.101.214.234 (Oracle Ubuntu 22.04, ~956 MB RAM)
- User: `tgbot` (system, /var/lib/tg-voice-transcriber)
- Service uptime ~hours, ~63 MB RSS
- Connected as `@ComebackPlay`
- 18 channels auto-tracked
- Top-N mode: **5**
- Threshold (fallback): 6
- Frequency: 30 min
- Delivery channel: `-1003764834613` (private "Digest" channel, public username `@mydigestchannel`)
- Single Groq key in pool (5 of original 6 keys are restricted)

## Known issues / debt

- **Groq org-wide restriction risk.** The original 6 keys were all restricted simultaneously by Groq's anti-abuse system. The current single key works but could meet the same fate. If it fails, switch to OpenRouter or appeal to Groq support.
- **Synthetic test data left noise in the Digest channel.** A handful of test digest messages with fake `@steam_free_games_test` username are in the channel history. User can manually delete them.
- **No `/digest` command for unsub-by-current-channels list.** Currently `/digest unsub @name` requires knowing the username. Could add a numbered-list interactive flow.
- **DEBUG log level was active at one point during debugging.** Now back to INFO.
