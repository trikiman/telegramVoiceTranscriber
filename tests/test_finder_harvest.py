"""Tests for finder.harvest pure core: link extraction, state, flood breaker."""

from __future__ import annotations

from tg_voice_transcriber.finder.harvest import (
    Candidate,
    FloodBreaker,
    HarvestState,
    extract_bot_links,
    looks_vpnish,
)

# --- extract_bot_links -----------------------------------------------------

def test_extract_basic_link_with_token() -> None:
    text = "Grab it here https://t.me/fastvpn_bot?start=REF123 today!"
    assert extract_bot_links(text) == [("fastvpn_bot", "REF123")]


def test_extract_at_mention_and_dedup() -> None:
    text = "Try @CoolVPNbot — link: t.me/coolvpnbot again and @coolvpnbot"
    # all three normalise to the same lowercased username → one entry
    assert extract_bot_links(text) == [("coolvpnbot", None)]


def test_extract_ignores_non_bot_usernames() -> None:
    text = "Join @vpnchannel and t.me/somechannel and read @user"
    assert extract_bot_links(text) == []


def test_extract_multiple_distinct_bots_preserve_order() -> None:
    text = "@alpha_bot then https://t.me/beta_bot?start=X then t.me/gamma_bot"
    assert extract_bot_links(text) == [
        ("alpha_bot", None),
        ("beta_bot", "X"),
        ("gamma_bot", None),
    ]


def test_extract_empty() -> None:
    assert extract_bot_links("") == []
    assert extract_bot_links("nothing to see") == []


def test_looks_vpnish() -> None:
    assert looks_vpnish("Free VPN Deals", None)
    assert looks_vpnish(None, "myvpn_channel")
    assert looks_vpnish("Прокси и обход", None)
    assert not looks_vpnish("Cat memes", "catlover")


# --- Candidate -------------------------------------------------------------

def test_candidate_build_url_variants() -> None:
    assert Candidate("foo_bot", None, "t", "s").build_url() == "https://t.me/foo_bot"
    assert Candidate("foo_bot", "TOK", "t", "s").build_url() == "https://t.me/foo_bot?start=TOK"
    assert Candidate("x", None, "t", "s", url="https://t.me/x?start=Z").build_url() == "https://t.me/x?start=Z"


# --- HarvestState ----------------------------------------------------------

def test_state_seen_tracking_normalizes() -> None:
    s = HarvestState()
    assert not s.already_seen("@FooBot")
    s.mark_seen("@FooBot")
    assert s.already_seen("foobot")


def test_state_stops_only_when_quota_and_long_trial_met() -> None:
    s = HarvestState(target_count=2, require_30_day=True)
    s.record_filed("a_bot", 7)
    assert not s.should_stop()  # 1 bot, no long trial
    s.record_filed("b_bot", 10)
    assert not s.should_stop()  # quota met but still no 30-day
    assert s.needs_only_long_trial()
    # short offers no longer worth filing once quota met + owe a long trial
    assert not s.should_file(7)
    assert s.should_file(30)
    s.record_filed("c_bot", 30)
    assert s.has_long_trial
    assert s.should_stop()


def test_state_no_30day_requirement_stops_at_quota() -> None:
    s = HarvestState(target_count=2, require_30_day=False)
    s.record_filed("a_bot", 3)
    assert not s.should_stop()
    s.record_filed("b_bot", 5)
    assert s.should_stop()


def test_state_should_file_under_quota_accepts_any_good() -> None:
    s = HarvestState(target_count=3, require_30_day=True)
    assert s.should_file(None)  # unknown duration, still under quota
    assert s.should_file(7)


def test_state_budget_exhausted() -> None:
    s = HarvestState(max_start_attempts=2)
    assert not s.budget_exhausted()
    s.record_start_attempt()
    s.record_start_attempt()
    assert s.budget_exhausted()


# --- FloodBreaker ----------------------------------------------------------

def test_flood_breaker_trips_on_single_long_wait() -> None:
    b = FloodBreaker(single_trip_s=300)
    b.record(301)
    assert b.tripped


def test_flood_breaker_trips_on_too_many_events() -> None:
    b = FloodBreaker(max_events=3, single_trip_s=10_000, cumulative_trip_s=10_000)
    b.record(5)
    b.record(5)
    assert not b.tripped
    b.record(5)
    assert b.tripped


def test_flood_breaker_trips_on_cumulative_wait() -> None:
    b = FloodBreaker(cumulative_trip_s=100, single_trip_s=10_000, max_events=999)
    b.record(60)
    assert not b.tripped
    b.record(60)
    assert b.tripped


def test_flood_breaker_stays_untripped_under_thresholds() -> None:
    b = FloodBreaker()
    b.record(10)
    b.record(10)
    assert not b.tripped
