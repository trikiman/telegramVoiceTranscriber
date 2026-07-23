# Milestone v1.2 Requirements

### Finder
- [ ] **FINDER-01**: Fetch sponsored messages (ads) and proxy sponsor channels to identify potential VPN/proxy offers.
- [ ] **FINDER-02**: Evaluate fetched ads using an LLM to determine if they offer a VPN/proxy trial of 10 or more days for 0 or 1 RUB.
- [ ] **FINDER-03**: Ignore offers that do not meet the criteria or are suspected scams.
- [ ] **FINDER-04**: Send a `/start` message to the bots that pass the evaluation, including any referral payload if provided by the ad link.
- [ ] **FINDER-05**: Automatically mute the bots after sending the `/start` message to avoid notification spam.
- [ ] **FINDER-06**: Automatically add the successfully started bots to a specific Telegram Dialog Filter folder (e.g., "10 дней vpn").
- [ ] **FINDER-07**: Rate-limit the `/start` commands and schedule the fetching process to avoid triggering Telegram's spam prevention mechanisms.
