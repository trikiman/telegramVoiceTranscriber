---
gsd_state_version: 1.0
milestone: v1.3
milestone_name: Active VPN Bot Harvester
current_phase_name: complete
status: Milestone v1.3 shipped — deployed to VPS, running autonomously daily
stopped_at: n/a (milestone complete, 2026-07-24)
last_updated: "2026-07-24T09:35:00.000Z"
last_activity: 2026-07-24
progress:
  total_phases: 1
  completed_phases: 1
  total_plans: 1
  completed_plans: 1
---

# STATE.md

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-05-15)

**Core value:** Every DM voice note (incoming and outgoing) gets a readable Russian/English transcript posted as a reply within seconds, without any paid service. v1.1 adds an LLM-filtered channel-digest subsystem so the user can follow many channels without notification overload.
**Current focus:** None — v1.3 shipped. Awaiting next milestone.

## Current Position

Phase: 9 (Active VPN Bot Harvester) — Complete
Plan: 09-01 (written + executed 2026-07-24)
Status: Live-verified, dedupe-hardened, deployed to VPS with a daily systemd
timer. 152 tests green, ruff clean. Milestone v1.3 fully shipped — the
harvester now runs autonomously without operator involvement.
Last activity: 2026-07-24

## Milestone

**Archived:** v1.3 — Active VPN Bot Harvester (shipped 2026-07-24)
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
| 9 | VPN Harvester | v1.3 | Complete (2026-07-24) |

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
  requirements reset, then live-verified 2026-07-24: 5/5 bots (all 30-day)
  collected into "10+ days vpn" (folder id=13, 12→17 peers). Milestone shipped.
  **Resolved 2026-07-24 (follow-up session):** the harvester is now deployed
  on the VPS proper (was previously only run from the local dev machine).
  `/opt/tg-voice-transcriber` is a real `git` checkout of `origin/master`
  (was hand-copied files before, with no `finder/` module at all — old copy
  backed up at `/opt/tg-voice-transcriber.bak-20260724090819`). A dedupe gap
  was also found and fixed: the harvester judged/filed candidates but never
  recorded them in `found_offers`, so a second run would have re-judged and
  potentially re-`/start`ed the same bots. `tg-voice-harvester.timer` now
  runs `scripts/harvest_vpn_bots.py` once daily (10:00 local ± 2h random
  spread, `Persistent=true`) via a oneshot `tg-voice-harvester.service`,
  fully independent of `tg-voice-transcriber.service` — a harvester issue
  can never affect DM transcription. Verified end-to-end via `systemd-run`
  with the real env file: dry-run found 5/5 candidates (all 30-day).
- **Main userbot session invalidated 2026-07-23 18:11 UTC (`AuthKeyDuplicatedError`)** —
  root cause: local scripts on this machine used `.local/userbot.session`
  (the SAME account/session as the VPS, phone `+79166076650`) around the same
  time the VPS service was running it, so Telegram revoked the shared auth key.
  VPS service `tg-voice-transcriber` was crash-looping on restart; stopped
  2026-07-24 07:19 UTC to avoid hammering Telegram. **Needs interactive
  re-login**: run `python scripts/login.py` locally (fresh SMS/2FA), copy the
  new `.local/userbot.session` to the VPS at
  `/var/lib/tg-voice-transcriber/userbot.session` (owner `tgbot:tgbot`, mode
  `0600`), then `sudo systemctl start tg-voice-transcriber`. Going forward,
  do NOT run local scripts against `.local/userbot.session` while the VPS
  service is live — the finder scripts already correctly use a separate
  `finder_session_path` (phone `+79958993023`) and did not cause this.
- **Groq key SPOF** — single working key after org-wide restriction; needs fallback provider or appeal.
- **Test-data noise in digest channel** — a handful of synthetic test digest messages with fake `@steam_free_games_test` username remain in history. User can manually delete.
- **No interactive `/digest unsub` numbered list** — current `/digest unsub @name` requires knowing the channel username.
- **Missing per-phase artifacts for phases 3–6** — directories and PLAN/SUMMARY files do not exist on disk. The work is in git but not in `.planning/phases/`. This shows up as 4 × W006 warnings in `gsd-health`. Acceptable for v1.0 (already shipped under autonomous mode) but should be backfilled if a retroactive audit is ever required.

## Next Step

Milestone v1.3 is fully shipped — the harvester runs autonomously on the VPS
with no operator involvement required. One loose end remains, unrelated to
the harvester:

1. **Re-authenticate the main userbot** (see Known Issues above) — the VPS
   voice-transcription + digest service is currently down
   (`AuthKeyDuplicatedError`, main account, phone `+79166076650`). The
   finder/harvester use a separate account and are unaffected and already
   running independently via the timer.

## Session

**Last session:** 2026-07-24T09:35:00.000Z
**Stopped at:** Milestone v1.3 fully shipped (deployed + autonomous); main
userbot re-auth remains the only open follow-up, tracked above.
**Resume file:** None
