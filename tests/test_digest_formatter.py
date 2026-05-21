"""Tests for digest formatter."""

from __future__ import annotations

import time

from tg_voice_transcriber.digest.formatter import format_digest
from tg_voice_transcriber.digest.scorer import ScoredPost


def _make_post(
    post_id: int,
    channel_title: str,
    username: str | None,
    score: int,
    summary: str,
    channel_id: int = -1001,
    message_id: int = 100,
) -> ScoredPost:
    return ScoredPost(
        post_id=post_id,
        channel_id=channel_id,
        message_id=message_id,
        channel_title=channel_title,
        channel_username=username,
        score=score,
        summary=summary,
        original_text="original text here",
    )


class TestFormatDigest:
    def test_empty_when_all_below_threshold(self):
        posts = [_make_post(1, "Ch", "ch", 4, "low score")]
        result = format_digest(posts, threshold=7, window_start=time.time(), window_end=time.time())
        assert result == []

    def test_single_post_above_threshold(self):
        posts = [_make_post(1, "AIHub", "aihub", 8, "New Claude model released")]
        result = format_digest(posts, threshold=7, window_start=time.time(), window_end=time.time())
        assert len(result) == 1
        assert "AIHub" in result[0] or "aihub" in result[0]
        assert "New Claude model released" in result[0]
        assert "1 of 1 relevant" in result[0]

    def test_grouped_by_channel(self):
        posts = [
            _make_post(1, "AIHub", "aihub", 8, "AI news 1", message_id=100),
            _make_post(2, "AIHub", "aihub", 9, "AI news 2", message_id=101),
            _make_post(3, "PyWeek", "pyweek", 7, "Python news", message_id=200, channel_id=-1002),
        ]
        result = format_digest(posts, threshold=7, window_start=time.time(), window_end=time.time())
        assert len(result) == 1
        msg = result[0]
        assert msg.count("📎") == 2  # two channels
        assert "3 of 3 relevant" in msg

    def test_mixed_above_below_threshold(self):
        posts = [
            _make_post(1, "Ch", "ch", 9, "Keep me"),
            _make_post(2, "Ch", "ch", 3, "Skip me"),
        ]
        result = format_digest(posts, threshold=7, window_start=time.time(), window_end=time.time())
        assert len(result) == 1
        assert "Keep me" in result[0]
        assert "Skip me" not in result[0]
        assert "1 of 2 relevant" in result[0]

    def test_public_channel_link(self):
        posts = [_make_post(1, "AIHub", "aihub", 8, "news", message_id=123)]
        result = format_digest(posts, threshold=7, window_start=time.time(), window_end=time.time())
        assert "https://t.me/aihub/123" in result[0]

    def test_private_channel_link(self):
        posts = [_make_post(1, "Private", None, 8, "news", channel_id=-1001234567890, message_id=45)]
        result = format_digest(posts, threshold=7, window_start=time.time(), window_end=time.time())
        assert "https://t.me/c/1234567890/45" in result[0]

    def test_fallback_summary_when_llm_empty(self):
        posts = [_make_post(1, "Ch", "ch", 8, "")]  # empty summary from LLM
        result = format_digest(posts, threshold=7, window_start=time.time(), window_end=time.time())
        # Falls back to first chars of original text
        assert "original text here" in result[0]



class TestTopNMode:
    """Top-N mode: always include top N posts regardless of threshold."""

    def test_top_n_includes_low_scored_posts(self):
        """Legacy v1.1 behavior preserved when top_n_floor=0."""
        posts = [
            _make_post(1, "Ch", "ch", 9, "high"),
            _make_post(2, "Ch", "ch", 4, "mid"),
            _make_post(3, "Ch", "ch", 1, "low"),
        ]
        result = format_digest(
            posts, threshold=7, window_start=1, window_end=2, top_n=5, top_n_floor=0
        )
        assert len(result) == 1
        # All three should appear because top_n=5 and we only have 3
        assert "high" in result[0]
        assert "mid" in result[0]
        assert "low" in result[0]
        assert "top 3 of 3 scanned" in result[0]

    def test_top_n_caps_at_n(self):
        posts = [_make_post(i, "Ch", "ch", 10 - i, f"item{i}") for i in range(1, 11)]
        result = format_digest(
            posts, threshold=7, window_start=1, window_end=2, top_n=3, top_n_floor=0
        )
        assert "top 3 of 10 scanned" in result[0]
        # Top 3 should be highest scores (item1=9, item2=8, item3=7)
        assert "item1" in result[0]
        assert "item2" in result[0]
        assert "item3" in result[0]
        assert "item10" not in result[0]

    def test_top_n_empty_buffer(self):
        result = format_digest([], threshold=7, window_start=1, window_end=2, top_n=5)
        assert result == []

    def test_top_n_zero_falls_back_to_threshold(self):
        posts = [_make_post(1, "Ch", "ch", 4, "below threshold")]
        result = format_digest(
            posts, threshold=7, window_start=1, window_end=2, top_n=0
        )
        # top_n=0 == threshold mode == nothing meets threshold 7
        assert result == []

    def test_score_label_in_output(self):
        posts = [_make_post(1, "Ch", "ch", 8, "ok")]
        result = format_digest(
            posts, threshold=7, window_start=1, window_end=2
        )
        assert "[8/10]" in result[0]
