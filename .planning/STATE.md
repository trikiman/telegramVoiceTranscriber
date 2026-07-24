---
gsd_state_version: 1.0
milestone: v1.3
milestone_name: Active VPN Bot Harvester
current_phase_name: implementation
status: Phase 9 code complete + unit-verified; live VPS run pending
stopped_at: awaiting operator live verification (2026-07-24)
last_updated: "2026-07-24T06:20:00.000Z"
last_activity: 2026-07-24
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
**Current focus:** Milestone v1.3 (Active VPN Bot Harvester) — running global search to seed the 10 дней vpn folder with 5 bots.

## Current Position

Phase: 9 (Active VPN Bot Harvester)
Plan: 09-01 (written + executed 2026-07-24)
Status: Code complete + unit-verified (156 tests green, ruff clean). Live VPS run pending.
Last activity: 2026-07-24

## Milestone

**Current:** v1.3 — Active VPN Bot Harvester (seed folder with 5 bots including 30-day trials)
**Archived:** v1.2 — VPN Trial Finder (shipped 2026-07-23)
**Archived:** v1.1 — Channel Digest (shipped 2026-05-14)
**Previous:** v1.0 — personal userbot for DM voice-note transcription (shipped 2026-05-12)

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
| 8 | VPN Trial Finder | v1.2 | Complete |
| 9 | VPN Harvester | v1.3 | Code complete (live pending) |

## Accumulated Context

- **Live deployment:** Oracle Cloud Ubuntu VPS at `158.101.214.234`, SSH key at `e:\Projects\vless\oracle_vless_key`, user `ubuntu`. Service runs as `tg-voice-transcriber.service` under system user `tgbot` from `/var/lib/tg-voice-transcriber`. Connected as `@ComebackPlay`.
- **Voice transcription:** pivoted from local faster-whisper to **Groq cloud** (`whisper-large-v3-turbo`) — the 956 MB VPS could not run whisper-small in usable time. Audio leaves the VPS, but the host was already a single-tenant box so the privacy model is unchanged.
- **Channel digest (v1.1):** SQLite-backed (`/var/lib/tg-voice-transcriber/digest.db`), Groq Llama-3.3-70B batched scorer, top-N mode (default 5) + threshold fallback, 30 min schedule, delivered to private channel `-1003764834613` (`@mydigestchannel`). Currently auto-tracks 18 channels.
- **Groq key pool:** built for 6-key rotation; 5 of 6 keys are currently restricted org-wide. Single working key is a known SPOF — fallback plan is OpenRouter or Groq support appeal.
- **Stack:** Python 3.10+, Telethon 1.43.x (`<2.0`), faster-whisper 1.2.x + CTranslate2 4.7.x retained as dependencies but not actively used in the live path; Groq HTTP client (`groq_client.py`) is shared between whisper and digest.
- **Workflow config:** YOLO mode, Standard granularity, parallel execution, planning docs committed to git, all quality gates enabled, model profile `inherit`. Phases 3–6 were executed in autonomous mode without per-phase artifacts (no PLAN/SUMMARY files on disk for those four — work is captured in git history under `feat(03)..feat(06)` commits).

## Known Issues / Debt

- **v1.3 harvester was never live-verified before 2026-07-24** — prior sessions
  marked HARVEST-01..04 done while the script crashed on import, targeted the
  wrong folder, and had a folder-clobber bug. Fixed + unit-tested in Phase 9;
  requirements reset and HARVEST-05 (live run) added. Live VPS run still pending.
- **Groq key SPOF** — single working key after org-wide restriction; needs fallback provider or appeal.
- **Test-data noise in digest channel** — a handful of synthetic test digest messages with fake `@steam_free_games_test` username remain in history. User can manually delete.
- **No interactive `/digest unsub` numbered list** — current `/digest unsub @name` requires knowing the channel username.
- **Missing per-phase artifacts for phases 3–6** — directories and PLAN/SUMMARY files do not exist on disk. The work is in git but not in `.planning/phases/`. This shows up as 4 × W006 warnings in `gsd-health`. Acceptable for v1.0 (already shipped under autonomous mode) but should be backfilled if a retroactive audit is ever required.

## Next Step

Live verification on the VPS finder account (the only step that can't be done
off-box). On the VPS, in the project dir, as the finder account:

1. `python scripts/set-finder-folder.py --list` then `python scripts/set-finder-folder.py`
   (pins "10+ days vpn" by id in finder.db).
2. `python scripts/harvest_vpn_bots.py --dry-run` (safe preview — no writes).
3. `python scripts/harvest_vpn_bots.py` (live: files 5 bots incl. one 30-day).

If few candidates appear, widen channel subscriptions first:
`python scripts/discover-channels.py` → `python scripts/subscribe-channels.py --from-log --commit`.

## Session

**Last session:** 2026-07-17T15:57:29.536Z
**Stopped at:** context exhaustion at 100% (2026-07-17)
**Resume file:** None
