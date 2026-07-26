# Milestone v1.3 Requirements

> **Integrity note (2026-07-24):** these were previously marked `[x] Satisfied`
> without a live run. In reality the harvester crashed on import
> (`create_chat_client` missing), targeted the wrong folder name, and had a
> folder-clobber bug — so nothing was ever collected. Reset to reflect reality,
> then re-verified: Phase 9 code fixed + unit-tested, and a live run on
> 2026-07-24 collected 5/5 bots (all 30-day) into "10+ days vpn" (id=13).
> Milestone v1.3 complete.

### Harvester
- [x] **HARVEST-01**: Script can search Telegram globally for VPN trials *(code
  + import path fixed; global-search source implemented — live yield depends on
  the account's channel subscriptions, verify on VPS)*
- [x] **HARVEST-02**: Script extracts bot usernames from search result texts
  *(covered by `extract_bot_links`, unit-tested)*
- [x] **HARVEST-03**: Script runs extracted offers through the existing
  OfferJudge *(wired; judge unchanged)*
- [x] **HARVEST-04**: Script stops once 5 qualifying bots are found, requiring at
  least one 30-day trial *(stop logic in `HarvestState`, unit-tested)*
- [x] **HARVEST-05**: Live verification on the finder account — dry-run listed
  5 candidates (all 30-day), live run started/muted/filed all 5 into folder
  id=13 "10+ days vpn" (12→17 peers, no clobbering) on 2026-07-24.

### Traceability

| REQ-ID | Description | Phase | Status |
|--------|-------------|-------|--------|
| HARVEST-01 | Search Telegram globally for VPN trials | Phase 9 | [x] Satisfied (live) |
| HARVEST-02 | Extract bot usernames from search texts | Phase 9 | [x] Satisfied (unit-tested) |
| HARVEST-03 | Evaluate offers using OfferJudge | Phase 9 | [x] Satisfied |
| HARVEST-04 | Stop at 5 qualifying bots with one 30-day trial | Phase 9 | [x] Satisfied (unit-tested + live) |
| HARVEST-05 | Live end-to-end run files 5 bots | Phase 9 | [x] Satisfied (2026-07-24) |

## Milestone v1.4 Requirements — Live Offer Verification (Phase 10)

> **Why this milestone exists:** live-verification on 2026-07-26 found the
> "autonomous" v1.3 harvester had a **100% failure rate on its 4 most recent
> adds** — every bot filed based on ad/search-snippet text turned out to lie
> when actually opened (24h claimed as 30d, 2d claimed as 30d-via-referral,
> a 14d offer gated behind submitting a review screenshot, and a 90d offer
> whose own live welcome text buried "requires paying for a subscription
> first"). Root cause: the harvester never checked a candidate bot's REAL
> `/start` welcome screen — only the ad/post/search-snippet text that
> discovered it. This is the same failure mode that debunked `@Ultaclub_bot`
> weeks earlier (see memory `finder-llm-budget.md`), now happening
> systematically.

### Harvester — Live Verification
- [x] **HARVEST-06**: Judge rejects "free" offers conditional on payment
  (e.g. "оплатив месяц вы получаете +30 дней") or proof-submission (e.g.
  "скиньте скриншот отзыва"), not just stated duration/price. *(Two concrete
  reject rules added to `judge.py`'s system prompt; spot-checked against the
  real model on both debunked cases — `vpn_chel_bot` (pay-first 90d) and
  `freedaysynatra_bot` (review-gated 14d) — both correctly rejected, a
  genuine no-conditions 14d control case still correctly accepted.)*
- [x] **HARVEST-07**: Harvester verifies a candidate's LIVE `/start` welcome
  screen before filing it — ad/search-snippet text alone is demonstrably
  unreliable. *(New `finder/verify.py::fetch_bot_welcome` reads the bot's
  real reply after `/start` — fixing a bug ported from `probe-bots.py` where
  the outgoing `/start` message itself could be misread as the bot's reply
  on a slow-to-respond bot. `harvest_vpn_bots.py::_process_candidate` now
  judges TWICE: stage-1 on ad text as a pre-filter, stage-2 on the live
  welcome screen — only stage-2's verdict files the bot, using the VERIFIED
  trial_days, not the ad-claimed ones. `found_offers` gained a
  `verified_good` column (guarded, idempotent migration, `SCHEMA_VERSION`
  2) so a debunked ad is remembered and never re-`/start`ed on a future run.
  Live-verified on the VPS 2026-07-26: a real run correctly rejected all 4
  candidates surfaced that day (`@aliusvpn_bot`, `@easygorobot`,
  `@freeskyvpn_bot`, `@easygo_vpn_bot`) after their live welcome screens
  didn't hold up — 0/5 filed, the correct outcome when no genuine offer
  exists, versus the prior behavior of blindly filing all 4.)*
- [x] **HARVEST-08**: Sponsored-ad discovery source's real yield is measured
  and documented, not assumed. *(Live run of
  `harvest_vpn_bots.py --dry-run --no-search --no-feeds` against the finder
  account's 50 subscribed channels on 2026-07-26: **31/50 channels (62%)
  returned ≥1 sponsored ad, 59 ads total.** Confirms the source is live and
  contributing, not dead weight — it is NOT the bottleneck; the bottleneck
  was the missing live-verification step, now fixed by HARVEST-07.)*
- [x] **HARVEST-09**: Folder audit sweep re-verifies previously-filed bots
  and removes any that are CONFIRMED bad by live re-check. *(New
  `scripts/audit-folder-bots.py`, reusing `finder/verify.py` + `OfferJudge`
  + the clobber-proof removal pattern from `remove-bots-from-folder.py`.
  **Design correction found during the live run**: re-`/start`ing an
  already-claimed bot usually shows its account state — "subscription
  expired", "renew now" — not a fresh offer screen, which fails the judge's
  cheap pre-filter for a reason unrelated to the original offer's honesty.
  An early version of this script treated that as failure and would have
  wrongly removed 14 legitimate bots. Fixed to keep three buckets — good /
  confirmed-bad (judge explicitly rejected a REAL offer claim in the live
  text) / inconclusive (pre-filtered or no reply — kept, logged, never
  auto-removed). Live sweep of the 18-peer folder on 2026-07-26: 1 OK, 17
  inconclusive (kept), of which 3 were separately confirmed bad by their
  actual judged rejection reason (`@nnvpn_iobot` — 30d but 3₽/day, over the
  0-1₽ bar; `@oko_review_bot`, `@MyBatyaReview_bot` — review-gated) and
  manually removed. 4 bait-and-switch bots found earlier the same session
  (`@sotka_install_bot`, `@greenvpn_rbot`, `@freedaysynatra_bot`,
  `@vpn_chel_bot`) were also manually removed. Folder went 22 → 15
  legitimate peers.)*
- [ ] **HARVEST-10** *(known gap, not yet fixed)*: `finder/scheduler.py`'s
  passive v1.2 path judges sponsored-ad text directly and has the same
  "ad copy can lie" gap HARVEST-07 fixes for the active harvester — not yet
  retrofitted. Low priority: this path is currently dormant (`finder_enabled`
  gated on `finder_phone`/`groq_api_key` both being set for a SEPARATE
  scheduler instance from the harvester's own connection).

### Traceability (v1.4)

| REQ-ID | Description | Phase | Status |
|--------|-------------|-------|--------|
| HARVEST-06 | Judge rejects payment/proof-conditional "free" offers | Phase 10 | [x] Satisfied (2026-07-26) |
| HARVEST-07 | Live `/start` welcome-screen verification before filing | Phase 10 | [x] Satisfied (2026-07-26, live-verified on VPS) |
| HARVEST-08 | Sponsored-ad source yield measured with a concrete number | Phase 10 | [x] Satisfied (31/50 channels, 59 ads) |
| HARVEST-09 | Folder audit sweep removes confirmed-bad already-filed bots | Phase 10 | [x] Satisfied (2026-07-26, 7 bots removed: 4 bait-and-switch + 3 confirmed-bad) |
| HARVEST-10 | Retrofit live verification into the passive v1.2 scheduler | — | [ ] Known gap, not in scope (dormant path) |
