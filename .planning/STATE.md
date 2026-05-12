# STATE.md

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-05-12)

**Core value:** Every DM voice note (incoming and outgoing) gets a readable Russian/English transcript posted as a reply within seconds, without any paid service.
**Current focus:** Phase 1 — Bootstrap & Session

## Current Position

Phase: 1 — Bootstrap & Session
Plan: —
Status: Not started (roadmap approved, ready to plan Phase 1)
Last activity: 2026-05-12 — Milestone v1.0 initialized, research + requirements + roadmap complete

## Milestone

**Current:** v1.0 — personal userbot for DM voice-note transcription (Russian + English)
**Previous:** (none — this is the first milestone)

## Roadmap at a glance

| # | Phase | Status |
|---|-------|--------|
| 1 | Bootstrap & Session | Not started |
| 2 | Audio Pipeline | Not started |
| 3 | Transcription Engine | Not started |
| 4 | Event Wiring & Reply UX | Not started |
| 5 | Hardening | Not started |
| 6 | VPS Deployment | Not started |

## Accumulated Context

- Target deployment host: Oracle Cloud Ubuntu VPS at `158.101.214.234`, SSH key at `e:\Projects\vless\oracle_vless_key`, user `ubuntu`. Existing vless service runs there — new userbot must coexist.
- Voice languages in scope: Russian + English, auto-detect per message.
- Faster-whisper model size decided: `small` multilingual on `int8` CPU.
- Stack verified against PyPI on 2026-05-12: Telethon 1.43.x (pin `<2.0`), faster-whisper 1.2.x, CTranslate2 4.7.x (ARM64 wheels confirmed).
- First interactive Telegram login must happen from the user's local machine (not the VPS) to avoid datacenter-IP ban risk; session file transferred afterwards.
- Workflow config: YOLO mode, Standard granularity, parallel execution allowed, planning docs committed to git, all quality gates enabled (research / plan-check / verifier), model profile `inherit`.

## Next Step

`/gsd-plan-phase 1` — research + plan Phase 1 (Bootstrap & Session).
