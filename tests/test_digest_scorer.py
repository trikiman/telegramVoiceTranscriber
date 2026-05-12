"""Tests for digest scorer (LLM batch relevance)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tg_voice_transcriber.digest.scorer import DigestScorer


def _make_groq_response(results: list[dict]) -> dict:
    """Build a mock Groq chat_completion response."""
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"results": results}),
                }
            }
        ]
    }


def _make_posts(n: int) -> list[dict]:
    return [
        {
            "id": i,
            "channel_id": -1000 - i,
            "message_id": 100 + i,
            "text": f"Post content {i}",
            "channel_title": f"Channel {i}",
            "channel_username": f"chan{i}",
        }
        for i in range(1, n + 1)
    ]


class TestScoreBatch:
    async def test_empty_batch_returns_empty(self):
        groq = MagicMock()
        scorer = DigestScorer(groq)
        result = await scorer.score_batch([], "prefs")
        assert result == []

    async def test_successful_scoring(self):
        groq = MagicMock()
        groq.chat_completion = AsyncMock(
            return_value=_make_groq_response([
                {"id": 1, "score": 9, "summary": "Very important"},
                {"id": 2, "score": 4, "summary": "Meh"},
            ])
        )
        scorer = DigestScorer(groq)
        posts = _make_posts(2)
        result = await scorer.score_batch(posts, "user cares about X")

        assert len(result) == 2
        assert result[0].score == 9
        assert result[0].summary == "Very important"
        assert result[1].score == 4

    async def test_clamp_score_range(self):
        groq = MagicMock()
        groq.chat_completion = AsyncMock(
            return_value=_make_groq_response([
                {"id": 1, "score": 15, "summary": "clamped high"},
                {"id": 2, "score": -3, "summary": "clamped low"},
            ])
        )
        scorer = DigestScorer(groq)
        posts = _make_posts(2)
        result = await scorer.score_batch(posts, "prefs")
        assert result[0].score == 10
        assert result[1].score == 1

    async def test_bad_json_falls_through_retry(self):
        groq = MagicMock()
        groq.chat_completion = AsyncMock(
            side_effect=[
                {"choices": [{"message": {"content": "not json"}}]},
                _make_groq_response([{"id": 1, "score": 8, "summary": "ok"}]),
            ]
        )
        scorer = DigestScorer(groq)
        posts = _make_posts(1)
        result = await scorer.score_batch(posts, "prefs")
        assert result[0].score == 8

    async def test_unrecoverable_failure_returns_zero_scored(self):
        groq = MagicMock()
        groq.chat_completion = AsyncMock(
            return_value={"choices": [{"message": {"content": "not json ever"}}]}
        )
        scorer = DigestScorer(groq)
        posts = _make_posts(2)
        result = await scorer.score_batch(posts, "prefs")
        assert all(p.score == 0 for p in result)
        assert len(result) == 2

    async def test_missing_post_id_in_response(self):
        groq = MagicMock()
        groq.chat_completion = AsyncMock(
            return_value=_make_groq_response([
                {"id": 1, "score": 9, "summary": "only 1"}
                # post 2 missing from response
            ])
        )
        scorer = DigestScorer(groq)
        posts = _make_posts(2)
        result = await scorer.score_batch(posts, "prefs")
        assert len(result) == 2
        assert result[0].score == 9
        assert result[1].score == 0  # missing from LLM → 0
