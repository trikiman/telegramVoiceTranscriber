# STATE.md

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-05-12)

**Core value:** Every DM voice note (incoming and outgoing) gets a readable Russian/English transcript posted as a reply within seconds, without any paid service. v1.1 adds an LLM-filtered channel-digest subsystem so the user can follow many channels without notification overload.
**Current focus:** Milestone v1.1 complete — ready to archive.

## Current Position

Phase: 7 — Channel Digest (v1.1)
Plan: 07-01 (complete)
Status: All milestone v1.1 work implemented, tested, and deployed live on the Oracle VPS. Ready for `/gsd-complete-milestone`.
Last activity: 2026-05-14 — Phase 7 shipped (top-N mode + self-contained digest summaries, deployed)

## Milestone

**Current:** v1.1 — Channel Digest (LLM-filtered subscribed-channel summaries delivered to Saved Messages)
**Previous:** v1.0 — personal userbot for DM voice-note transcription (Russian + English), shipped 2026-05-12

## Roadmap at a glance

| # | Phase | Milestone | Status |
|---|-------|-----------|--------|
| 1 | Bootstrap & Session | v1.0 | Complete |
| 2 | Audio Pipeline | v1.0 | Complete |
| 3 | Transcription Engine | v1.0 | Complete (pivoted to Groq cloud) |
| 4 | Event Wiring & Reply UX | v1.0 | Complete |
| 5 | Hardening | v1.0 | Complete |
| 6 | VPS Deployment | v1.0 | Complete (live on 158.101.214.234) |
| 7 | Channel Digest | v1.1 | Complete |

## Accumulated Context

- **Live deployment:** Oracle Cloud Ubuntu VPS at `158.101.214.234`, SSH key at `e:\Projects\vless\oracle_vless_key`, user `ubuntu`. Service runs as `tg-voice-transcriber.service` under system user `tgbot` from `/var/lib/tg-voice-transcriber`. Connected as `@ComebackPlay`.
- **Voice transcription:** pivoted from local faster-whisper to **Groq cloud** (`whisper-large-v3-turbo`) — the 956 MB VPS could not run whisper-small in usable time. Audio leaves the VPS, but the host was already a single-tenant box so the privacy model is unchanged.
- **Channel digest (v1.1):** SQLite-backed (`/var/lib/tg-voice-transcriber/digest.db`), Groq Llama-3.3-70B batched scorer, top-N mode (default 5) + threshold fallback, 30 min schedule, delivered to private channel `-1003764834613` (`@mydigestchannel`). Currently auto-tracks 18 channels.
- **Groq key pool:** built for 6-key rotation; 5 of 6 keys are currently restricted org-wide. Single working key is a known SPOF — fallback plan is OpenRouter or Groq support appeal.
- **Stack:** Python 3.10+, Telethon 1.43.x (`<2.0`), faster-whisper 1.2.x + CTranslate2 4.7.x retained as dependencies but not actively used in the live path; Groq HTTP client (`groq_client.py`) is shared between whisper and digest.
- **Workflow config:** YOLO mode, Standard granularity, parallel execution, planning docs committed to git, all quality gates enabled, model profile `inherit`. Phases 3–6 were executed in autonomous mode without per-phase artifacts (no PLAN/SUMMARY files on disk for those four — work is captured in git history under `feat(03)..feat(06)` commits).

## Known Issues / Debt

- **Groq key SPOF** — single working key after org-wide restriction; needs fallback provider or appeal.
- **Test-data noise in digest channel** — a handful of synthetic test digest messages with fake `@steam_free_games_test` username remain in history. User can manually delete.
- **No interactive `/digest unsub` numbered list** — current `/digest unsub @name` requires knowing the channel username.
- **Missing per-phase artifacts for phases 3–6** — directories and PLAN/SUMMARY files do not exist on disk. The work is in git but not in `.planning/phases/`. This shows up as 4 × W006 warnings in `gsd-health`. Acceptable for v1.0 (already shipped under autonomous mode) but should be backfilled if a retroactive audit is ever required.

## Next Step

`/gsd-complete-milestone` — archive milestone v1.1 (Phase 7 → Channel Digest). Optionally run `/gsd-verify-work 7` first for conversational UAT against the live digest, since no UAT.md exists for phase 7.
