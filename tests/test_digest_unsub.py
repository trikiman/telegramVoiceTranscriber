"""Tests for /digest unsub resolution: numeric ID, @username, reply-to-forwarded."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_voice_transcriber.digest.commands import (
    _cmd_sub,
    _cmd_unsub,
    _normalize_channel_id,
    _resolve_unsub_target,
)
from tg_voice_transcriber.digest.db import (
    add_tracked_channel,
    init_db,
    is_channel_blocked,
    is_channel_tracked,
)


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "digest.db"
    await init_db(path)
    return path


def _make_event(text: str = "/digest unsub", reply_message=None):
    """Build a stub event with the same shape _resolve_unsub_target reads."""
    msg = SimpleNamespace(
        is_reply=reply_message is not None,
        get_reply_message=AsyncMock(return_value=reply_message),
    )
    return SimpleNamespace(message=msg)


def _make_forwarded_message(*, chat_id=None, chat=None, from_name=None):
    """Build a stub replied-to message that has a `.forward` attribute."""
    fwd = SimpleNamespace(chat_id=chat_id, chat=chat, from_name=from_name)
    return SimpleNamespace(forward=fwd)


class TestNormalizeChannelId:
    def test_marked_form(self):
        assert _normalize_channel_id("-1001930196351") == -1001930196351

    def test_bare_positive_gets_prefix(self):
        assert _normalize_channel_id("1930196351") == -1001930196351

    def test_negative_passthrough(self):
        assert _normalize_channel_id("-12345") == -12345

    def test_non_numeric_returns_none(self):
        assert _normalize_channel_id("@kyloai") is None
        assert _normalize_channel_id("") is None


class TestResolveUnsubTarget:
    async def test_numeric_marked(self, db_path):
        client = AsyncMock()
        event = _make_event()
        cid, title, username, err = await _resolve_unsub_target(client, "-1001930196351", event)
        assert err is None
        assert cid == -1001930196351
        client.get_entity.assert_not_called()

    async def test_numeric_bare_positive(self, db_path):
        client = AsyncMock()
        event = _make_event()
        cid, _, _, err = await _resolve_unsub_target(client, "1930196351", event)
        assert err is None
        assert cid == -1001930196351

    async def test_username_resolves_via_client(self, db_path):
        # Telethon-like Channel entity: id=raw, megagroup attr present
        entity = SimpleNamespace(
            id=12345,
            title="Kylo AI",
            username="kyloai",
            megagroup=False,
            broadcast=True,
        )
        client = AsyncMock()
        client.get_entity.return_value = entity
        event = _make_event()

        cid, title, username, err = await _resolve_unsub_target(client, "@kyloai", event)
        assert err is None
        assert cid == int("-10012345")
        assert username == "kyloai"
        assert title == "Kylo AI"
        client.get_entity.assert_called_once_with("kyloai")

    async def test_reply_to_forwarded_uses_chat_id(self, db_path):
        replied = _make_forwarded_message(
            chat_id=-1001930196351,
            chat=SimpleNamespace(id=1930196351, title="Коды ТГ", username=None),
        )
        event = _make_event(reply_message=replied)
        client = AsyncMock()

        cid, title, username, err = await _resolve_unsub_target(client, "", event)
        assert err is None
        assert cid == -1001930196351
        assert title == "Коды ТГ"

    async def test_reply_to_forwarded_anonymous(self, db_path):
        # Forward source hidden — chat_id None, chat None, only from_name
        replied = _make_forwarded_message(chat_id=None, chat=None, from_name="Anonymous Channel")
        event = _make_event(reply_message=replied)
        client = AsyncMock()

        cid, _, _, err = await _resolve_unsub_target(client, "", event)
        assert cid is None
        assert err is not None
        assert "hidden" in err.lower() or "anonymous" in err.lower()

    async def test_reply_to_non_forwarded(self, db_path):
        replied = SimpleNamespace(forward=None)
        event = _make_event(reply_message=replied)
        client = AsyncMock()

        cid, _, _, err = await _resolve_unsub_target(client, "", event)
        assert cid is None
        assert "not a forwarded" in err

    async def test_no_args_no_reply_returns_usage(self, db_path):
        event = _make_event()
        client = AsyncMock()
        cid, _, _, err = await _resolve_unsub_target(client, "", event)
        assert cid is None
        assert "usage" in err.lower()


class TestCmdUnsub:
    async def test_unsub_removes_from_tracked_and_blocks(self, db_path):
        cid = -1001930196351
        await add_tracked_channel(db_path, cid, "Коды ТГ")

        client = AsyncMock()
        replied = _make_forwarded_message(
            chat_id=cid,
            chat=SimpleNamespace(id=1930196351, title="Коды ТГ", username=None),
        )
        event = _make_event(reply_message=replied)

        reply = await _cmd_unsub(client, db_path, "", event)

        assert "Unsubscribed" in reply or "blocklist" in reply
        assert await is_channel_tracked(db_path, cid) is False
        assert await is_channel_blocked(db_path, cid) is True

    async def test_unsub_unknown_channel_still_blocks(self, db_path):
        cid = -1001234567890
        client = AsyncMock()
        event = _make_event()

        reply = await _cmd_unsub(client, db_path, str(cid), event)
        assert "blocklist" in reply
        assert await is_channel_blocked(db_path, cid) is True


class TestCmdSub:
    async def test_sub_removes_from_blocklist(self, db_path):
        """/digest sub <id> removes from blocklist AND adds to tracked_channels."""
        from tg_voice_transcriber.digest.db import (
            add_blocked_channel,
            is_channel_tracked,
        )

        cid = -1001930196351
        await add_blocked_channel(db_path, cid, "Коды ТГ")

        client = AsyncMock()
        client.get_entity = AsyncMock(side_effect=Exception("offline"))
        reply = await _cmd_sub(client, db_path, str(cid))

        # New v9.2 message format: "<label> (<id>): <effects>"
        assert "removed from blocklist" in reply
        assert "added to tracked channels" in reply
        assert await is_channel_blocked(db_path, cid) is False
        # Now also tracked
        assert await is_channel_tracked(db_path, cid) is True

    async def test_sub_idempotent_when_not_blocked(self, db_path):
        """If not blocked and not yet tracked, /digest sub still adds to tracked."""
        from tg_voice_transcriber.digest.db import is_channel_tracked

        client = AsyncMock()
        client.get_entity = AsyncMock(side_effect=Exception("offline"))
        cid = -1009999999999
        reply = await _cmd_sub(client, db_path, str(cid))
        # First call: was not blocked, but was added to tracked
        assert "added to tracked channels" in reply
        assert "removed from blocklist" not in reply
        assert await is_channel_tracked(db_path, cid) is True

        # Second call: now both fully no-op
        reply2 = await _cmd_sub(client, db_path, str(cid))
        assert "already tracked and not blocked" in reply2
