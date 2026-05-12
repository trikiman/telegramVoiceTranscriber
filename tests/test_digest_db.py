"""Tests for digest SQLite helpers."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tg_voice_transcriber.digest.db import (
    add_tracked_channel,
    buffer_post,
    compute_post_hash,
    drain_buffer,
    init_db,
    is_channel_tracked,
    list_tracked_channels,
    load_config,
    recent_stats,
    record_cycle_stats,
    remove_tracked_channel,
    save_config,
    seen_recently,
)


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "digest.db"
    await init_db(path)
    return path


class TestConfig:
    async def test_default_config_loads(self, db_path):
        cfg = await load_config(db_path)
        assert cfg.delivery_chat_id is None
        assert cfg.user_prefs_text == ""
        assert cfg.threshold == 7
        assert cfg.frequency_s == 1800
        assert cfg.paused is False
        assert cfg.ready is False  # no delivery chat set

    async def test_save_and_load(self, db_path):
        cfg = await load_config(db_path)
        cfg.delivery_chat_id = -1001234567890
        cfg.user_prefs_text = "AI, Python, no crypto"
        cfg.threshold = 8
        cfg.frequency_s = 3600
        cfg.paused = True
        await save_config(db_path, cfg)

        reloaded = await load_config(db_path)
        assert reloaded.delivery_chat_id == -1001234567890
        assert reloaded.user_prefs_text == "AI, Python, no crypto"
        assert reloaded.threshold == 8
        assert reloaded.frequency_s == 3600
        assert reloaded.paused is True
        assert reloaded.ready is True


class TestTrackedChannels:
    async def test_add_and_list(self, db_path):
        added = await add_tracked_channel(db_path, -1001, "Channel One", "chone")
        assert added is True

        channels = await list_tracked_channels(db_path)
        assert len(channels) == 1
        assert channels[0]["channel_id"] == -1001
        assert channels[0]["channel_title"] == "Channel One"
        assert channels[0]["channel_username"] == "chone"

    async def test_duplicate_returns_false(self, db_path):
        await add_tracked_channel(db_path, -1001, "Ch", None)
        added_again = await add_tracked_channel(db_path, -1001, "Ch", None)
        assert added_again is False

    async def test_is_channel_tracked(self, db_path):
        await add_tracked_channel(db_path, -1001, "Ch", None)
        assert await is_channel_tracked(db_path, -1001) is True
        assert await is_channel_tracked(db_path, -9999) is False

    async def test_remove(self, db_path):
        await add_tracked_channel(db_path, -1001, "Ch", None)
        removed = await remove_tracked_channel(db_path, -1001)
        assert removed is True
        removed_again = await remove_tracked_channel(db_path, -1001)
        assert removed_again is False


class TestBuffer:
    async def test_buffer_and_drain(self, db_path):
        await buffer_post(db_path, -1001, 100, "First post")
        await buffer_post(db_path, -1001, 101, "Second post")

        posts = await drain_buffer(db_path)
        assert len(posts) == 2
        assert posts[0]["text"] == "First post"
        assert posts[1]["text"] == "Second post"

    async def test_drain_clears(self, db_path):
        await buffer_post(db_path, -1001, 100, "Post")
        await drain_buffer(db_path)
        posts_after = await drain_buffer(db_path)
        assert posts_after == []

    async def test_duplicate_buffer(self, db_path):
        first = await buffer_post(db_path, -1001, 100, "Post")
        second = await buffer_post(db_path, -1001, 100, "Post")
        assert first is True
        assert second is False


class TestDedupe:
    async def test_first_sighting(self, db_path):
        seen = await seen_recently(db_path, "abc123")
        assert seen is False

    async def test_second_sighting_returns_true(self, db_path):
        await seen_recently(db_path, "abc123")
        seen = await seen_recently(db_path, "abc123")
        assert seen is True

    async def test_post_hash_stable(self):
        h1 = compute_post_hash("Hello World")
        h2 = compute_post_hash("Hello World")
        assert h1 == h2

    async def test_post_hash_normalization(self):
        h1 = compute_post_hash("  Hello World  ")
        h2 = compute_post_hash("hello world")
        assert h1 == h2  # case-insensitive + stripped


class TestStats:
    async def test_record_and_aggregate(self, db_path):
        now = time.time()
        await record_cycle_stats(db_path, now - 1800, now - 1700, 50, 3)
        await record_cycle_stats(db_path, now - 900, now - 800, 40, 2)

        stats = await recent_stats(db_path, since=now - 86400)
        assert stats["scanned"] == 90
        assert stats["delivered"] == 5
        assert stats["cycles"] == 2
        assert stats["errors"] == 0

    async def test_error_flag(self, db_path):
        now = time.time()
        await record_cycle_stats(db_path, now - 900, now, 10, 0, llm_error=True)
        stats = await recent_stats(db_path, since=0)
        assert stats["errors"] == 1
