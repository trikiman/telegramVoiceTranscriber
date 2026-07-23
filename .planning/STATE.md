---
gsd_state_version: 1.0
milestone: v1.2
milestone_name: VPN Trial Finder
current_phase_name: planning
status: Phase 8 planning
stopped_at: context exhaustion at 100% (2026-07-17)
last_updated: "2026-07-17T15:57:29.545Z"
last_activity: 2026-05-15
progress:
  total_phases: 0
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
---

# STATE.md

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-05-15)

**Core value:** Every DM voice note (incoming and outgoing) gets a readable Russian/English transcript posted as a reply within seconds, without any paid service. v1.1 adds an LLM-filtered channel-digest subsystem so the user can follow many channels without notification overload.
**Current focus:** Milestone v1.2 (VPN Trial Finder) — integrating and verifying Phase 8 finder code.

## Current Position

Phase: 8 (VPN Trial Finder)
Plan: None
Status: Planning
Last activity: 2026-05-15

## Milestone

**Current:** v1.2 — VPN Trial Finder (extract 10+ day VPN trials from ads, auto-start, mute, file to folder)
**Archived:** v1.1 — Channel Digest (LLM-filtered subscribed-channel summaries delivered to Saved Messages), shipped 2026-05-14
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

`/gsd-plan-phase` — to create a PLAN.md for Phase 8.

## Session

**Last session:** 2026-07-17T15:57:29.536Z
**Stopped at:** context exhaustion at 100% (2026-07-17)
**Resume file:** None
