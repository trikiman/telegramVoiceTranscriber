"""Tests for the top-N floor behavior in the digest formatter (Phase 9.1)."""

from __future__ import annotations

import time

from tg_voice_transcriber.digest.formatter import format_digest
from tg_voice_transcriber.digest.scorer import ScoredPost


def _post(score: int, summary: str = "summary", post_id: int = 1) -> ScoredPost:
    return ScoredPost(
        post_id=post_id,
        channel_id=-1001,
        message_id=100,
        channel_title="Channel",
        channel_username="ch",
        score=score,
        summary=summary,
        original_text="text",
    )


def _window():
    end = time.time()
    start = end - 1800
    return start, end


def test_top_n_floor_drops_below_threshold():
    """In top-N mode, posts below the floor must be filtered out."""
    posts = [_post(score=1, post_id=1), _post(score=2, post_id=2), _post(score=8, post_id=3)]
    start, end = _window()
    result = format_digest(
        posts,
        threshold=7,
        window_start=start,
        window_end=end,
        top_n=5,
        top_n_floor=4,
    )
    # Only the score=8 post should make it
    assert len(result) == 1
    assert "[8/10]" in result[0]
    assert "[1/10]" not in result[0]
    assert "[2/10]" not in result[0]


def test_top_n_floor_zero_means_no_floor():
    """A floor of 0 disables filtering — legacy v1.1 behavior (ships trash)."""
    posts = [_post(score=1, post_id=1), _post(score=2, post_id=2)]
    start, end = _window()
    result = format_digest(
        posts, threshold=7, window_start=start, window_end=end, top_n=5, top_n_floor=0
    )
    # Both posts delivered when floor=0
    assert len(result) == 1
    assert "[1/10]" in result[0]
    assert "[2/10]" in result[0]


def test_top_n_floor_all_below_returns_empty():
    """If every post is below the floor, return [] — skip the cycle entirely."""
    posts = [_post(score=1, post_id=1), _post(score=2, post_id=2), _post(score=3, post_id=3)]
    start, end = _window()
    result = format_digest(
        posts, threshold=7, window_start=start, window_end=end, top_n=5, top_n_floor=4
    )
    assert result == []


def test_top_n_floor_only_applies_in_top_n_mode():
    """Threshold mode ignores top_n_floor (only the threshold matters)."""
    posts = [_post(score=1), _post(score=8)]
    start, end = _window()
    # threshold=7 → only score=8 qualifies
    result = format_digest(
        posts,
        threshold=7,
        window_start=start,
        window_end=end,
        top_n=None,
        top_n_floor=4,
    )
    assert len(result) == 1
    assert "[8/10]" in result[0]
    assert "[1/10]" not in result[0]


def test_top_n_floor_default_is_4():
    """The function signature default for top_n_floor must be 4."""
    posts = [_post(score=3, post_id=1)]
    start, end = _window()
    # Don't pass top_n_floor explicitly; default should drop score<4
    result = format_digest(
        posts, threshold=7, window_start=start, window_end=end, top_n=5
    )
    assert result == []  # score=3 < default floor 4


def test_top_n_floor_keeps_higher_when_some_below():
    """Mix of below-floor and above-floor: keep only above-floor."""
    posts = [
        _post(score=2, summary="trash", post_id=1),
        _post(score=4, summary="border", post_id=2),
        _post(score=9, summary="great", post_id=3),
    ]
    start, end = _window()
    result = format_digest(
        posts, threshold=7, window_start=start, window_end=end, top_n=5, top_n_floor=4
    )
    assert len(result) == 1
    body = result[0]
    assert "[9/10]" in body and "great" in body
    assert "[4/10]" in body and "border" in body
    assert "[2/10]" not in body and "trash" not in body


def test_top_n_floor_top_n_limits_count_after_filter():
    """When more posts pass the floor than top_n allows, take the top N."""
    posts = [_post(score=10, post_id=1), _post(score=8, post_id=2), _post(score=6, post_id=3)]
    start, end = _window()
    result = format_digest(
        posts, threshold=7, window_start=start, window_end=end, top_n=2, top_n_floor=4
    )
    body = result[0]
    assert "[10/10]" in body
    assert "[8/10]" in body
    # post_id=3 (score=6) should be excluded by top_n=2
    assert "[6/10]" not in body
