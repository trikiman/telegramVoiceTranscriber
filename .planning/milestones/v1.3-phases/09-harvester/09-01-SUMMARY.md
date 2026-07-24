# Summary 09-01: Active VPN Bot Harvester

**Phase:** 9 — Active VPN Bot Harvester (v1.3)
**Executed:** 2026-07-24
**Status:** Code complete + unit-verified. Live verification pending on VPS.

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
- Full suite green (156 tests); `ruff` clean on all touched files.

## Deviations from plan

- Did NOT deploy to the VPS or run against the live Telegram account — that
  requires the finder session/API credentials, which are not available in the
  build environment. Deliberately left as the final live step for the operator,
  with a `--dry-run` mode and exact commands to make first-run trustworthy.

## Follow-ups / open items

- Live verification on the VPS: pin folder, dry-run, then live run.
- If discovery yields few candidates, subscribe to more VPN channels first
  (`discover-channels.py` → `subscribe-channels.py --from-log --commit`).
- The Groq single-working-key SPOF (from v1.1) still applies; OpenRouter
  fallback mitigates it.
