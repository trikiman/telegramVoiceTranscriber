"""Integration tests: harvest_vpn_bots._process_candidate dedupes via finder.db.

Verifies two things:
1. The dedupe fix from the v1.3 live-verification follow-up: the harvester
   must record every processed offer in `found_offers` so a second run (e.g.
   the next day's scheduled harvest) never re-judges or re-`/start`s the same
   offer, burning the /start safety budget for nothing.
2. The two-stage verification fix (Phase 10): a candidate is judged TWICE —
   once on the ad/search/post text (stage 1), once on the bot's live `/start`
   welcome screen (stage 2) — and only filed if BOTH agree. This is what
   catches bots whose ad copy overstates the real offer.
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


def _offer(*, trial_days: int = 30, summary: str = "NewVPN — 30 days free") -> SimpleNamespace:
    return SimpleNamespace(
        is_good_trial=True,
        scam_suspected=False,
        target_bot="@newvpn_bot",
        trial_days=trial_days,
        trial_price_rub=0.0,
        start_param=None,
        summary=summary,
    )


def _rejected(summary: str = "not actually free") -> SimpleNamespace:
    return SimpleNamespace(
        is_good_trial=False,
        scam_suspected=False,
        target_bot="@newvpn_bot",
        trial_days=None,
        trial_price_rub=None,
        start_param=None,
        summary=summary,
    )


class _FakeJudge:
    """Returns queued results in order: 1st call = stage-1 (ad text),
    2nd call = stage-2 (live text). Defaults both calls to the same good
    offer for tests that only care about dedupe, not the two-stage split."""

    def __init__(self, results: list | None = None) -> None:
        self.calls = 0
        self._results = results if results is not None else [_offer(), _offer()]

    async def judge_offer(self, **kwargs):
        result = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        return result


class _FakeStarter:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def start_and_mute(self, username: str, start_param=None) -> bool:
        self.started.append(username)
        return True


class _FakeEntity:
    id = 12345
    access_hash = 999
    username = "newvpn_bot"


class _FakeClient:
    """Supplies a live welcome reply so stage-2 verification has real text."""

    def __init__(self, welcome_text: str = "NewVPN — 30 дней бесплатно, никаких условий") -> None:
        self._welcome_text = welcome_text

    async def get_entity(self, _username):
        return _FakeEntity()

    async def get_messages(self, _entity, limit: int = 5):
        if not self._welcome_text:
            return []
        return [SimpleNamespace(message=self._welcome_text, out=False, reply_markup=None)]


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
    judge = _FakeJudge()  # stage-1 good, stage-2 good
    starter = _FakeStarter()
    state = HarvestState(target_count=5, require_30_day=True)
    cand = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")

    await harvester._process_candidate(
        cand, judge=judge, starter=starter, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state, dry_run=False,
        db_path=db_path,
    )

    assert judge.calls == 2  # stage-1 (ad text) + stage-2 (live welcome)
    assert starter.started == ["@newvpn_bot"]
    assert state.bots_added == 1

    offer_hash = finder_db.compute_offer_hash(cand.text)
    assert await finder_db.offer_already_found(db_path, "@newvpn_bot", offer_hash)

    offers = await finder_db.list_found_offers(db_path)
    assert offers[0]["verified_good"] == 1


async def test_live_check_rejects_bait_and_switch_offer(db_path):
    """The core Phase-10 fix: ad text says 30 days, but the bot's REAL
    welcome screen (stage 2) says otherwise -> must NOT be filed, but must
    still be recorded (verified_good=0) so we never re-/start it again."""
    judge = _FakeJudge(results=[
        _offer(trial_days=30, summary="NewVPN — 30 дней бесплатно"),  # stage-1 (ad)
        _rejected("NewVPN — на самом деле 1 день"),                     # stage-2 (live)
    ])
    starter = _FakeStarter()
    state = HarvestState(target_count=5, require_30_day=True)
    cand = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")

    await harvester._process_candidate(
        cand, judge=judge, starter=starter, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state, dry_run=False,
        db_path=db_path,
    )

    assert judge.calls == 2
    assert starter.started == ["@newvpn_bot"]  # bot IS /started — that's how we verify it
    assert state.bots_added == 0  # but NOT filed

    offer_hash = finder_db.compute_offer_hash(cand.text)
    assert await finder_db.offer_already_found(db_path, "@newvpn_bot", offer_hash)
    offers = await finder_db.list_found_offers(db_path)
    assert offers[0]["verified_good"] == 0


async def test_live_check_rejects_when_bot_never_replies(db_path):
    """No live welcome text at all -> reject. Never fall back to trusting the
    ad text just because the bot was slow/unresponsive."""
    judge = _FakeJudge(results=[_offer()])  # only stage-1 needed; stage-2 skipped (no text)
    starter = _FakeStarter()
    state = HarvestState(target_count=5, require_30_day=True)
    cand = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")

    await harvester._process_candidate(
        cand, judge=judge, starter=starter, client=_FakeClient(welcome_text=""),
        folder=SimpleNamespace(id=13), state=state, dry_run=False,
        db_path=db_path,
    )

    assert judge.calls == 1  # stage-2 never called — no live text to judge
    assert state.bots_added == 0


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


async def test_second_run_skips_previously_debunked_offer(db_path):
    """A bot that was /started and rejected at stage-2 must ALSO not be
    re-/started on a later run for the same ad text — otherwise the harvester
    would keep hammering the same debunked bot every day forever."""
    cand = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")

    judge1 = _FakeJudge(results=[_offer(), _rejected()])
    starter1 = _FakeStarter()
    state1 = HarvestState(target_count=5, require_30_day=True)
    await harvester._process_candidate(
        cand, judge=judge1, starter=starter1, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state1, dry_run=False,
        db_path=db_path,
    )
    assert state1.bots_added == 0
    assert starter1.started == ["@newvpn_bot"]

    judge2 = _FakeJudge()
    starter2 = _FakeStarter()
    state2 = HarvestState(target_count=5, require_30_day=True)
    await harvester._process_candidate(
        cand, judge=judge2, starter=starter2, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state2, dry_run=False,
        db_path=db_path,
    )

    assert judge2.calls == 0
    assert starter2.started == []  # never re-/started
    assert state2.bots_added == 0


async def test_dry_run_does_not_bypass_dedupe_check(db_path):
    """--dry-run should still skip already-collected offers (it only skips
    the *writes*: /start, mute, folder add). Dry-run also never performs live
    verification (that would require /starting the bot), so it only spends
    one stage-1 judge call per candidate."""
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


async def test_dry_run_only_judges_ad_text_once(db_path):
    """A dry-run on a NEW (not-yet-collected) candidate previews the
    unverified ad-text judgment without /starting the bot."""
    judge = _FakeJudge()
    starter = _FakeStarter()
    state = HarvestState(target_count=5, require_30_day=True)
    cand = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")

    await harvester._process_candidate(
        cand, judge=judge, starter=starter, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state, dry_run=True,
        db_path=db_path,
    )

    assert judge.calls == 1  # stage-1 only — dry-run never /starts to verify
    assert starter.started == []
    assert state.bots_added == 1  # preview counts toward the printed quota


async def test_same_bot_reworded_ad_does_not_burn_another_start(db_path):
    """A bot we already opened must not cost a second /start just because its
    ad was reworded. Dedupe keyed only on (bot, offer_hash) let the same bots
    reappear as 'new' every run and drained the ban-safety budget before the
    harvester ever reached an unseen bot."""
    cand1 = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")
    cand2 = Candidate("newvpn_bot", None, "NewVPN — special 60 day offer!", "global-search")

    await harvester._process_candidate(
        cand1, judge=_FakeJudge(), starter=_FakeStarter(), client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=HarvestState(target_count=5),
        dry_run=False, db_path=db_path,
    )

    judge2 = _FakeJudge()
    starter2 = _FakeStarter()
    state2 = HarvestState(target_count=5, require_30_day=True)
    await harvester._process_candidate(
        cand2, judge=judge2, starter=starter2, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=state2, dry_run=False,
        db_path=db_path, recheck_days=14,
    )

    assert judge2.calls == 0
    assert starter2.started == []  # budget preserved for genuinely unseen bots
    assert state2.bots_added == 0


async def test_bot_is_reexamined_once_the_recheck_window_lapses(db_path):
    """The skip is a staleness window, not a permanent ban — offers do change,
    so with recheck_days=0 the bot is opened and judged again."""
    cand1 = Candidate("newvpn_bot", None, "NewVPN 30 дней бесплатно", "global-search")
    cand2 = Candidate("newvpn_bot", None, "NewVPN — new 60 day promo!", "global-search")

    await harvester._process_candidate(
        cand1, judge=_FakeJudge(), starter=_FakeStarter(), client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=HarvestState(target_count=5),
        dry_run=False, db_path=db_path,
    )

    judge2 = _FakeJudge()
    starter2 = _FakeStarter()
    await harvester._process_candidate(
        cand2, judge=judge2, starter=starter2, client=_FakeClient(),
        folder=SimpleNamespace(id=13), state=HarvestState(target_count=5),
        dry_run=False, db_path=db_path, recheck_days=0,
    )

    assert judge2.calls == 2  # stage-1 + stage-2, same as any fresh candidate
    assert starter2.started == ["@newvpn_bot"]
