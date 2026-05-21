"""Tests for opt-in supergroup ingest (Phase 9.2)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_voice_transcriber.digest import db as digest_db
from tg_voice_transcriber.digest.ingest import register_digest_handler


class _FakeClient:
    """Stub Telethon client capturing handler registrations."""

    def __init__(self) -> None:
        self.handlers: list = []

    def on(self, _builder):
        def decorator(fn):
            self.handlers.append(fn)
            return fn
        return decorator


def _make_event(*, is_channel: bool, is_group: bool, chat_id: int, text: str, msg_id: int = 100):
    """Build a minimal Event-shaped object for the ingest handler."""
    chat = SimpleNamespace(title="X", username=None)
    return SimpleNamespace(
        is_channel=is_channel,
        is_group=is_group,
        chat_id=chat_id,
        message=SimpleNamespace(message=text, id=msg_id, media=None),
        get_chat=AsyncMock(return_value=chat),
    )


@pytest.mark.asyncio
async def test_dm_is_skipped():
    """is_channel=False → DM or basic group → never ingested."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "d.db"
        await digest_db.init_db(db_path)
        client = _FakeClient()
        register_digest_handler(client, db_path, default_track_all=True)
        handler = client.handlers[0]

        event = _make_event(is_channel=False, is_group=False, chat_id=12345, text="hello world this is long enough")
        await handler(event)
        # Buffer must be empty
        rows = await digest_db.drain_buffer(db_path)
        assert rows == []


@pytest.mark.asyncio
async def test_basic_group_is_skipped():
    """Basic group (is_channel=False, is_group=True) → skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "d.db"
        await digest_db.init_db(db_path)
        client = _FakeClient()
        register_digest_handler(client, db_path, default_track_all=True)
        handler = client.handlers[0]

        event = _make_event(is_channel=False, is_group=True, chat_id=-12345, text="message body that is long enough to pass the min length filter")
        await handler(event)
        rows = await digest_db.drain_buffer(db_path)
        assert rows == []


@pytest.mark.asyncio
async def test_broadcast_channel_auto_added():
    """Broadcast channel (is_channel=True, is_group=False) → auto-tracked + buffered."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "d.db"
        await digest_db.init_db(db_path)
        client = _FakeClient()
        register_digest_handler(client, db_path, default_track_all=True)
        handler = client.handlers[0]

        event = _make_event(
            is_channel=True, is_group=False, chat_id=-1001234567,
            text="A broadcast channel post that is more than 20 characters long",
        )
        await handler(event)
        # Tracked + buffered
        assert await digest_db.is_channel_tracked(db_path, -1001234567)
        rows = await digest_db.drain_buffer(db_path)
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_supergroup_not_auto_added_even_with_default_track_all():
    """Supergroup (is_channel=True, is_group=True) → NEVER auto-tracked.

    This is the key Phase 9.2 invariant: supergroups must be explicitly
    subscribed via /digest sub to avoid runaway noise.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "d.db"
        await digest_db.init_db(db_path)
        client = _FakeClient()
        register_digest_handler(client, db_path, default_track_all=True)
        handler = client.handlers[0]

        event = _make_event(
            is_channel=True, is_group=True, chat_id=-1009876543,
            text="A supergroup chat message long enough to pass the min length filter",
        )
        await handler(event)
        # NOT tracked, NOT buffered
        assert not await digest_db.is_channel_tracked(db_path, -1009876543)
        rows = await digest_db.drain_buffer(db_path)
        assert rows == []


@pytest.mark.asyncio
async def test_supergroup_is_ingested_when_explicitly_tracked():
    """If a supergroup is in tracked_channels, its posts are buffered."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "d.db"
        await digest_db.init_db(db_path)
        # Pre-track the supergroup (simulating /digest sub having been run)
        await digest_db.add_tracked_channel(db_path, -1009876543, "VibecoderChat", "vibecoderchat")

        client = _FakeClient()
        register_digest_handler(client, db_path, default_track_all=True)
        handler = client.handlers[0]

        event = _make_event(
            is_channel=True, is_group=True, chat_id=-1009876543,
            text="An interesting message from the tracked supergroup we explicitly subscribed to",
        )
        await handler(event)
        rows = await digest_db.drain_buffer(db_path)
        assert len(rows) == 1
        assert rows[0]["channel_id"] == -1009876543


@pytest.mark.asyncio
async def test_blocked_supergroup_is_skipped():
    """Even if a supergroup is tracked, blocklist takes precedence."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "d.db"
        await digest_db.init_db(db_path)
        await digest_db.add_tracked_channel(db_path, -1009876543, "X", None)
        await digest_db.add_blocked_channel(db_path, -1009876543, "X", None)

        client = _FakeClient()
        register_digest_handler(client, db_path, default_track_all=True)
        handler = client.handlers[0]

        event = _make_event(
            is_channel=True, is_group=True, chat_id=-1009876543,
            text="this should be skipped because the supergroup is on the blocklist",
        )
        await handler(event)
        rows = await digest_db.drain_buffer(db_path)
        assert rows == []
