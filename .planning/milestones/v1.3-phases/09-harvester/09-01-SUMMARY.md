# Summary 09-01: Active VPN Bot Harvester

**Phase:** 9 — Active VPN Bot Harvester (v1.3)
**Executed:** 2026-07-24
**Status:** Complete. Code + unit-verified, then live-verified same day. Milestone v1.3 shipped.

## What changed

- **Fixed the import crash**: added `create_chat_client()` to `llm_failover.py`;
  the harvester imported a symbol that never existed, so it died on startup.
- **Rename-proof folder targeting**: tolerant title match + `find_folder_by_id`
  + `resolve_folder`; folder title is now config/env-driven
  (`TG_VOICE_FINDER_FOLDER_TITLE`, default `"10+ days vpn"`) and pinned by id in
  `finder.db`. New `scripts/set-finder-folder.py` heals the live DB.
- **Fixed the folder-clobber bug**: `add_peer_to_folder` re-fetches the live
  filter and mutates it in place, so multiple bots added in one run accumulate.
- **Robust harvester**: new `finder/harvest.py` (pure, tested core + 3 streamed
  discovery sources: global search, channel-feed scan, sponsored ads). Rewrote
  `scripts/harvest_vpn_bots.py` with fail-fast auth, `--dry-run`, a FloodWait
  circuit-breaker, a `/start` budget, and quota + 30-day stop logic.
- **Scheduler hardening**: v1.2 finder now uses the same rename-proof resolution
  (with id auto-heal) and survives FloodWaits.
- **Planning integrity**: reset the HARVEST requirements that were checked off
  without a live run.

## Tests

- `tests/test_finder_folder.py`, `tests/test_finder_harvest.py`, and new
  `create_chat_client` cases in `tests/test_llm_failover.py`.
- Full suite green (148 tests); `ruff` clean on all patch-touched files.

## Live verification (2026-07-24)

Local repo work from the same session had not been committed and was
recovered from a saved `git am` patch; re-applied cleanly, tests re-run green,
then pushed to `origin/master` (commit `69331ef`).

Run from the local dev machine (finder account already had a working session
and 12-peer folder from prior manual discovery/subscribe work this session —
VPS deploy of the harvester was not done, see Deviations):

1. `python scripts/set-finder-folder.py --list` → folder `id=13 '10+ days vpn'
   (12 peers)` visible.
2. `python scripts/set-finder-folder.py` → pinned by id in `finder.db`.
3. `python scripts/harvest_vpn_bots.py --dry-run` → 5/5 candidates found via
   global-search, all with 30-day trials (7 candidates seen).
4. `python scripts/harvest_vpn_bots.py` (live) → collected 5/5:
   `@oko_review_bot`, `@MyBatyaReview_bot`, `@zlupavpnbot`, `@YT_vpnbot`,
   `@luxevpn_bot` — all 30-day trials, all `/start`ed, all muted, all filed
   into folder id=13 (12→17 peers, confirming the clobber fix — no bot was
   overwritten). Dedupe recorded in `finder.db`.
5. Re-checked `set-finder-folder.py --list` post-run → `17 peers` confirmed.

**SUCCESS criteria met**: 5 bots in "10+ days vpn", all offering a 30-day
trial (exceeds the ≥1 requirement), all muted, deduped in `finder.db`.

## Deviations from plan

- The live run executed from the **local Windows dev machine**, not the VPS
  as originally planned — the finder account's session/db/logs already
  existed locally from earlier ad-hoc discovery work this session, and the
  VPS has no `finder/` module or harvester scripts deployed at all (checked:
  `find /opt/tg-voice-transcriber/src -iname '*finder*'` → empty). Harvester
  code is committed and pushed to `master`, ready for a VPS deploy
  (`git pull` + `pip install -e .`) if recurring/scheduled runs are wanted
  later — that step is not done.

## Follow-ups / open items

- **Unrelated incident found during this session**: the main userbot's
  Telegram session (`.local/userbot.session`, phone `+79166076650` — same
  account used by the live VPS service) was used locally around
  2026-07-23 21:14 MSK, causing Telegram to revoke the shared auth key
  (`AuthKeyDuplicatedError`) and crash the VPS `tg-voice-transcriber` service
  at 18:11 UTC the same day. The VPS service was crash-looping on auto-restart;
  stopped manually 2026-07-24 07:19 UTC. Needs interactive re-login
  (`python scripts/login.py` locally, SMS/2FA), then copy the new session file
  to the VPS and restart the service. See STATE.md Known Issues for exact
  steps. Root cause: local scripts must not touch `.local/userbot.session`
  while the VPS is running the same account — the finder scripts correctly
  use a separate `finder_session_path`/account and were not the cause.
- If discovery yields few candidates on a future run, subscribe to more VPN
  channels first (`discover-channels.py` → `subscribe-channels.py --from-log
  --commit`).
- The Groq single-working-key SPOF (from v1.1) still applies; OpenRouter
  fallback mitigates it.
