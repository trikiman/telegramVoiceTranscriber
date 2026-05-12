# STATE.md

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-05-12)

**Core value:** Every DM voice note (incoming and outgoing) gets a readable Russian/English transcript posted as a reply within seconds, without any paid service.
**Current focus:** Phase 1 — Bootstrap & Session

## Current Position

Phase: 6 — VPS Deployment (all phases complete)
Plan: —
Status: All phases implemented, ready for deployment
Last activity: 2026-05-12 — All 6 phases executed autonomously

## Milestone

**Current:** v1.0 — personal userbot for DM voice-note transcription (Russian + English)
**Previous:** (none — this is the first milestone)

## Roadmap at a glance

| # | Phase | Status |
|---|-------|--------|
| 1 | Bootstrap & Session | Complete |
| 2 | Audio Pipeline | Complete |
| 3 | Transcription Engine | Complete |
| 4 | Event Wiring & Reply UX | Complete |
| 5 | Hardening | Complete |
| 6 | VPS Deployment | Complete |

## Accumulated Context

- Target deployment host: Oracle Cloud Ubuntu VPS at `158.101.214.234`, SSH key at `e:\Projects\vless\oracle_vless_key`, user `ubuntu`. Existing vless service runs there — new userbot must coexist.
- Voice languages in scope: Russian + English, auto-detect per message.
- Faster-whisper model size decided: `small` multilingual on `int8` CPU.
- Stack verified against PyPI on 2026-05-12: Telethon 1.43.x (pin `<2.0`), faster-whisper 1.2.x, CTranslate2 4.7.x (ARM64 wheels confirmed).
- First interactive Telegram login must happen from the user's local machine (not the VPS) to avoid datacenter-IP ban risk; session file transferred afterwards.
- Workflow config: YOLO mode, Standard granularity, parallel execution allowed, planning docs committed to git, all quality gates enabled (research / plan-check / verifier), model profile `inherit`.

## Next Step

Deploy to VPS:
1. `python scripts/login.py` (local machine — create session)
2. SSH into VPS, run `deploy/setup-vps.sh`
3. Copy session file, fill env, start service

Then: `/gsd-complete-milestone` to archive.
