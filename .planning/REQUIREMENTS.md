# Milestone v1.3 Requirements

> **Integrity note (2026-07-24):** these were previously marked `[x] Satisfied`
> without a live run. In reality the harvester crashed on import
> (`create_chat_client` missing), targeted the wrong folder name, and had a
> folder-clobber bug — so nothing was ever collected. Reset to reflect reality.
> Phase 9 (2026-07-24) fixed the code and added unit tests; the boxes below are
> checked only for what is truly done and gated on live verification where noted.

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
- [ ] **HARVEST-05**: Live verification on the VPS finder account — dry-run lists
  candidates, then a live run files 5 bots (incl. one 30-day) into the folder
  *(cannot be done off-box; pending operator run)*

### Traceability

| REQ-ID | Description | Phase | Status |
|--------|-------------|-------|--------|
| HARVEST-01 | Search Telegram globally for VPN trials | Phase 9 | [x] Code done; live yield to verify |
| HARVEST-02 | Extract bot usernames from search texts | Phase 9 | [x] Satisfied (unit-tested) |
| HARVEST-03 | Evaluate offers using OfferJudge | Phase 9 | [x] Satisfied |
| HARVEST-04 | Stop at 5 qualifying bots with one 30-day trial | Phase 9 | [x] Satisfied (unit-tested) |
| HARVEST-05 | Live end-to-end run on VPS files 5 bots | Phase 9 | [ ] Pending operator run |
