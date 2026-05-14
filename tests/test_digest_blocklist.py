"""Tests for digest blocklist (schema v2) and the unsub→block flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from tg_voice_transcriber.digest.db import (
    add_blocked_channel,
    add_tracked_channel,
    init_db,
    is_channel_blocked,
    is_channel_tracked,
    list_blocked_channels,
    remove_blocked_channel,
    remove_tracked_channel,
)


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "digest.db"
    await init_db(path)
    return path


class TestBlocklist:
    async def test_add_remove_idempotent(self, db_path):
        cid = -1001930196351
        assert await add_blocked_channel(db_path, cid, "Test") is True
        assert await add_blocked_channel(db_path, cid, "Test") is False
        assert await is_channel_blocked(db_path, cid) is True

        assert await remove_blocked_channel(db_path, cid) is True
        assert await remove_blocked_channel(db_path, cid) is False
        assert await is_channel_blocked(db_path, cid) is False

    async def test_list_returns_recent_first(self, db_path):
        await add_blocked_channel(db_path, -1001, "A")
        await add_blocked_channel(db_path, -1002, "B")
        await add_blocked_channel(db_path, -1003, "C")
        rows = await list_blocked_channels(db_path)
        assert len(rows) == 3
        ids = [r["channel_id"] for r in rows]
        # ORDER BY blocked_at DESC — last-added first
        assert ids[0] == -1003

    async def test_unsub_to_block_flow(self, db_path):
        """Real-world flow: tracked → unsub removes from tracked + adds to blocklist."""
        cid = -1001930196351
        await add_tracked_channel(db_path, cid, "Коды ТГ")
        assert await is_channel_tracked(db_path, cid)

        # Simulate _cmd_unsub doing both ops
        assert await remove_tracked_channel(db_path, cid) is True
        assert await add_blocked_channel(db_path, cid, "Коды ТГ") is True

        assert await is_channel_tracked(db_path, cid) is False
        assert await is_channel_blocked(db_path, cid) is True

    async def test_re_init_idempotent(self, tmp_path: Path):
        """Schema v2 migration from a freshly-empty file is idempotent across restarts."""
        path = tmp_path / "digest.db"
        await init_db(path)
        await add_blocked_channel(path, -1001, "A")
        # Simulate process restart
        await init_db(path)
        assert await is_channel_blocked(path, -1001) is True
