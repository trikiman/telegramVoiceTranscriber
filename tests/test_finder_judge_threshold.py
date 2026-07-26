"""The duration threshold must hold even when the LLM says otherwise.

A live run filed @DartVPNBot with a 5-day trial into the "10+ days" folder:
the model returned is_good_trial=true for 5 days although the prompt said
under-10 must be rejected. Small models don't reliably honour a numeric rule
stated in prose, so the gate is enforced in code and pinned here.
"""

from __future__ import annotations

import json

import pytest

from tg_voice_transcriber.finder.judge import OfferJudge


class _ScriptedLLM:
    """Returns whatever verdict the test dictates, as the real client would."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def chat_completion(self, **_kwargs):
        return {"choices": [{"message": {"content": json.dumps(self._payload)}}]}


def _verdict(**overrides):
    payload = {
        "is_good_trial": True,
        "trial_days": 30,
        "trial_price_rub": 0.0,
        "scam_suspected": False,
        "target_bot": None,
        "summary": "SomeVPN",
    }
    payload.update(overrides)
    return payload


# Text must survive the cheap prefilter so the LLM path is actually exercised.
_OFFER = "SomeVPN — пробный период, бесплатно, дней"


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [1, 2, 5, 7, 9])
async def test_rejects_short_trial_even_if_llm_approves(days: int) -> None:
    judge = OfferJudge(_ScriptedLLM(_verdict(trial_days=days)), model="m")
    result = await judge.judge_offer(text=_OFFER, channel_id=0)
    assert result is not None
    assert result.is_good_trial is False, f"{days}d must not pass a 10-day floor"
    assert result.trial_days == days  # extraction preserved for the log


@pytest.mark.asyncio
async def test_rejects_unknown_duration_even_if_llm_approves() -> None:
    """'∞ дней' / unreadable duration must not count as a verified trial."""
    judge = OfferJudge(_ScriptedLLM(_verdict(trial_days=None)), model="m")
    result = await judge.judge_offer(text=_OFFER, channel_id=0)
    assert result is not None
    assert result.is_good_trial is False


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [10, 14, 30, 90])
async def test_accepts_trials_at_or_above_the_floor(days: int) -> None:
    judge = OfferJudge(_ScriptedLLM(_verdict(trial_days=days)), model="m")
    result = await judge.judge_offer(text=_OFFER, channel_id=0)
    assert result is not None
    assert result.is_good_trial is True
    assert result.trial_days == days


@pytest.mark.asyncio
async def test_threshold_is_configurable() -> None:
    """Lowering the bar to 7 days is a one-argument change, not a prompt edit."""
    judge = OfferJudge(_ScriptedLLM(_verdict(trial_days=7)), model="m", min_trial_days=7)
    result = await judge.judge_offer(text=_OFFER, channel_id=0)
    assert result is not None
    assert result.is_good_trial is True


@pytest.mark.asyncio
async def test_rejection_reason_is_visible_in_summary() -> None:
    judge = OfferJudge(_ScriptedLLM(_verdict(trial_days=5)), model="m")
    result = await judge.judge_offer(text=_OFFER, channel_id=0)
    assert result is not None
    assert "5d" in result.summary and "10d" in result.summary
