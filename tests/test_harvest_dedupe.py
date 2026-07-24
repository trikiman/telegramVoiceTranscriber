"""Integration tests: harvest_vpn_bots._process_candidate dedupes via finder.db.

Verifies the fix for the gap found during v1.3 live-verification follow-up:
the harvester judged/filed candidates but never recorded them in
`found_offers`, so a second run (e.g. the next day's scheduled harvest) would
re-judge and potentially re-/start the exact same offer, burning the /start
safety budget for nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import harvest_vpn_bots as harvester  # noqa: E402  (sys.path set above)
from tg_voice_transcriber.finder import db as finder_db
from tg_voice_transcriber.finder.harvest import Candidate, HarvestState


class _FakeJudge:
    """Records how many times judge_offer was called; always approves."""

    def __init__(self, trial_days: int = 30) -> None:
        self.calls = 0
        self.trial_days = trial_days

    async def judge_offer(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            is_good_trial=True,
            scam_suspected=False,
            target_bot="@newvpn_bot",
            trial_days=self.trial_days,
            trial_price_rub=0.0,
            start_param=None,
            summary="NewVPN — 30 days free",
        )


class _FakeStarter:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def start_and_mute(self, username: str, start_param=None) -> bool:
        self.started.append(username)
        return True


class _FakeEntity:
    id = 12345
    access_hash = 999


class _FakeClient:
    async def get_entity(self, _username):
        return _FakeEntity()


async def _fake_add_peer_to_folder(*args, **kwargs) -> bool:
    return True


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "finder.db"
    await finder_db.init_finder_db(path)
    return path


@pytest.fixture(autouse=True)
def _patch_add_peer(monkeypatch):
    monkeypatch.setattr(harvester, "add_peer_to_folder", _fake_add_peer_to_folder)


async def test_first_run_files_and_records_offer(db_path):
    judge = _FakeJudge()
    starter = _FakeStarter()
    state = HarvestState(target_count=5, require_30_day=True)
    cand = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")

    await harvester._process_candidate(
        cand, judge=judge, starter=starter, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state, dry_run=False,
        db_path=db_path,
    )

    assert judge.calls == 1
    assert starter.started == ["@newvpn_bot"]
    assert state.bots_added == 1

    offer_hash = finder_db.compute_offer_hash(cand.text)
    assert await finder_db.offer_already_found(db_path, "@newvpn_bot", offer_hash)


async def test_second_run_skips_previously_found_offer(db_path):
    """A later run (e.g. next day's scheduled harvest) must not re-judge or
    re-/start the same offer — this is the core of the dedupe fix."""
    cand = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")

    # First run: files it normally.
    judge1 = _FakeJudge()
    starter1 = _FakeStarter()
    state1 = HarvestState(target_count=5, require_30_day=True)
    await harvester._process_candidate(
        cand, judge=judge1, starter=starter1, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state1, dry_run=False,
        db_path=db_path,
    )
    assert state1.bots_added == 1

    # Second "run" (fresh state, simulating a new scheduled invocation) sees
    # the identical candidate again.
    judge2 = _FakeJudge()
    starter2 = _FakeStarter()
    state2 = HarvestState(target_count=5, require_30_day=True)
    await harvester._process_candidate(
        cand, judge=judge2, starter=starter2, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state2, dry_run=False,
        db_path=db_path,
    )

    # No LLM call, no /start, nothing filed — the offer was already collected.
    assert judge2.calls == 0
    assert starter2.started == []
    assert state2.bots_added == 0


async def test_dry_run_does_not_bypass_dedupe_check(db_path):
    """--dry-run should still skip already-collected offers (it only skips
    the *writes*: /start, mute, folder add)."""
    cand = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")

    judge1 = _FakeJudge()
    starter1 = _FakeStarter()
    state1 = HarvestState(target_count=5, require_30_day=True)
    await harvester._process_candidate(
        cand, judge=judge1, starter=starter1, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state1, dry_run=False,
        db_path=db_path,
    )

    judge2 = _FakeJudge()
    starter2 = _FakeStarter()
    state2 = HarvestState(target_count=5, require_30_day=True)
    await harvester._process_candidate(
        cand, judge=judge2, starter=starter2, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state2, dry_run=True,
        db_path=db_path,
    )

    assert judge2.calls == 0
    assert state2.bots_added == 0


async def test_different_offer_text_for_same_bot_is_not_deduped(db_path):
    """A genuinely new offer for a bot we've seen before (different text/hash)
    should still be judged and filed — dedupe is keyed on (bot, offer_hash),
    not bot alone."""
    cand1 = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")
    cand2 = Candidate("newvpn_bot", None, "NewVPN — special 60 day offer!", "global-search")

    judge1 = _FakeJudge()
    starter1 = _FakeStarter()
    state1 = HarvestState(target_count=5, require_30_day=True)
    await harvester._process_candidate(
        cand1, judge=judge1, starter=starter1, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state1, dry_run=False,
        db_path=db_path,
    )

    judge2 = _FakeJudge()
    starter2 = _FakeStarter()
    state2 = HarvestState(target_count=5, require_30_day=True)
    await harvester._process_candidate(
        cand2, judge=judge2, starter=starter2, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state2, dry_run=False,
        db_path=db_path,
    )

    assert judge2.calls == 1
    assert starter2.started == ["@newvpn_bot"]
