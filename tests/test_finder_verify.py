"""Tests for finder.verify — live welcome-screen fetch, the m.out bug fix."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tg_voice_transcriber.finder.verify import fetch_bot_welcome


class _Btn:
    def __init__(self, text: str) -> None:
        self.text = text


class _Row:
    def __init__(self, buttons: list[_Btn]) -> None:
        self.buttons = buttons


class _Markup:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows


def _msg(text: str, *, out: bool, markup=None):
    return SimpleNamespace(message=text, out=out, reply_markup=markup)


class _FakeClient:
    def __init__(self, messages: list) -> None:
        self._messages = messages

    async def get_messages(self, _entity, limit: int = 5):
        return self._messages[:limit]


@pytest.mark.asyncio
async def test_skips_own_outgoing_start_message() -> None:
    """The core bug: get_messages returns both directions, and a slow bot
    reply must not be shadowed by the /start command we just sent."""
    client = _FakeClient([
        _msg("/start abc123ref", out=True),   # our own outgoing /start
        _msg("Добро пожаловать! 14 дней бесплатно.", out=False),
    ])
    text, buttons = await fetch_bot_welcome(
        client, object(), wait_s_range=(0.0, 0.0), max_wait_s=0.0, poll_interval_s=0.0
    )
    assert text == "Добро пожаловать! 14 дней бесплатно."
    assert buttons == []


@pytest.mark.asyncio
async def test_returns_empty_when_bot_never_replies() -> None:
    """No incoming message at all -> empty result, never fall back to ad text."""
    client = _FakeClient([_msg("/start", out=True)])
    text, buttons = await fetch_bot_welcome(
        client, object(), wait_s_range=(0.0, 0.0), max_wait_s=0.0, poll_interval_s=0.0
    )
    assert text == ""
    assert buttons == []


@pytest.mark.asyncio
async def test_returns_empty_when_no_messages() -> None:
    client = _FakeClient([])
    text, buttons = await fetch_bot_welcome(
        client, object(), wait_s_range=(0.0, 0.0), max_wait_s=0.0, poll_interval_s=0.0
    )
    assert text == ""
    assert buttons == []


@pytest.mark.asyncio
async def test_skips_blank_incoming_messages() -> None:
    """A blank/whitespace-only incoming message (e.g. just a photo) doesn't
    count as the welcome text — keep scanning for a real one."""
    client = _FakeClient([
        _msg("/start", out=True),
        _msg("   ", out=False),
        _msg("Реальный текст приветствия", out=False),
    ])
    text, _ = await fetch_bot_welcome(
        client, object(), wait_s_range=(0.0, 0.0), max_wait_s=0.0, poll_interval_s=0.0
    )
    assert text == "Реальный текст приветствия"


class _SlowClient:
    """Replies only on the Nth read — models a bot that takes >5s to answer."""

    def __init__(self, messages: list, replies_on_call: int) -> None:
        self._messages = messages
        self._replies_on_call = replies_on_call
        self.calls = 0

    async def get_messages(self, _entity, limit: int = 5):
        self.calls += 1
        if self.calls < self._replies_on_call:
            return [_msg("/start", out=True)]  # only our own message so far
        return self._messages[:limit]


@pytest.mark.asyncio
async def test_polls_until_slow_bot_replies() -> None:
    """A bot answering after the initial wait must still be captured — reading
    once and giving up would reject a perfectly good bot as 'no reply'."""
    client = _SlowClient(
        [_msg("/start", out=True), _msg("Добро пожаловать! 30 дней бесплатно.", out=False)],
        replies_on_call=3,
    )
    text, _ = await fetch_bot_welcome(
        client, object(), wait_s_range=(0.0, 0.0), max_wait_s=5.0, poll_interval_s=0.0
    )
    assert text == "Добро пожаловать! 30 дней бесплатно."
    assert client.calls == 3


@pytest.mark.asyncio
async def test_gives_up_after_max_wait() -> None:
    """Polling is bounded — a bot that never replies doesn't hang the run."""
    client = _SlowClient([_msg("never", out=False)], replies_on_call=10_000)
    text, _ = await fetch_bot_welcome(
        client, object(), wait_s_range=(0.0, 0.0), max_wait_s=0.05, poll_interval_s=0.01
    )
    assert text == ""
    assert client.calls < 100  # bounded, not spinning forever


@pytest.mark.asyncio
async def test_collects_button_labels_from_welcome_message() -> None:
    markup = _Markup([_Row([_Btn("Получить прокси"), _Btn("Ещё один")])])
    client = _FakeClient([
        _msg("/start", out=True),
        _msg("Нажмите кнопку ниже", out=False, markup=markup),
    ])
    text, buttons = await fetch_bot_welcome(
        client, object(), wait_s_range=(0.0, 0.0), max_wait_s=0.0, poll_interval_s=0.0
    )
    assert text == "Нажмите кнопку ниже"
    assert buttons == ["Получить прокси", "Ещё один"]


# --- subscribe-gate pass-through -------------------------------------------
# Some bots hide their offer behind "join this channel first". Refusing to
# pass that gate means the real terms are never seen and the bot is rejected
# unread. We pass it — and MUTE every channel we join, per the standing rule.

from tg_voice_transcriber.finder import verify as _verify  # noqa: E402
from tg_voice_transcriber.finder.verify import (  # noqa: E402
    _is_joined_button,
    pass_subscribe_gate,
)


class _UrlBtn:
    def __init__(self, text: str, url: str) -> None:
        self.text = text
        self.url = url
        self.data = None


class _CbBtn:
    def __init__(self, text: str, data: bytes | None = None) -> None:
        self.text = text
        self.data = data
        self.url = None


class _GateMsg:
    def __init__(self, buttons: list) -> None:
        self.reply_markup = _Markup([_Row(buttons)])
        self.clicked: dict | None = None

    async def click(self, **kwargs):
        self.clicked = kwargs


class _GateClient:
    def __init__(self) -> None:
        self.joined: list[str] = []
        self.requests: list = []

    async def get_entity(self, uname):
        return SimpleNamespace(id=hash(uname) % 10_000, username=uname)

    async def __call__(self, request):
        self.requests.append(request)
        return True


def test_recognises_joined_button_in_multiple_languages() -> None:
    assert _is_joined_button(_CbBtn("✅ Я подписался"))
    assert _is_joined_button(_CbBtn("I subscribed"))
    # Persian — the case a Russian-only marker list silently missed.
    assert _is_joined_button(_CbBtn("✅ عضو شدم"))
    # Callback payload alone is enough, whatever the label says.
    assert _is_joined_button(_CbBtn("🔄", data=b"check_join"))
    # A plain join-the-channel link is NOT the confirmation button.
    assert not _is_joined_button(_UrlBtn("📢 Our channel", "https://t.me/somechan"))


@pytest.mark.asyncio
async def test_gate_joins_and_mutes_channel_then_confirms(monkeypatch) -> None:
    muted: list = []

    async def _fake_mute(_client, peer):
        muted.append(getattr(peer, "username", peer))
        return True

    monkeypatch.setattr(_verify, "mute_peer", _fake_mute)

    msg = _GateMsg([
        _UrlBtn("📢 عضویت در کانال", "https://t.me/required_chan"),
        _CbBtn("✅ عضو شدم", data=b"check_join"),
    ])
    client = _GateClient()

    passed = await pass_subscribe_gate(client, SimpleNamespace(username="gated_bot"), msg)

    assert passed is True
    assert muted == ["required_chan"], "the gate channel must be muted, not just joined"
    assert msg.clicked == {"data": b"check_join"}


@pytest.mark.asyncio
async def test_no_gate_means_no_action(monkeypatch) -> None:
    """An ordinary welcome screen must not be mistaken for a gate."""
    monkeypatch.setattr(_verify, "mute_peer", lambda *a, **k: None)
    msg = _GateMsg([_CbBtn("Получить пробные 10 дней", data=b"trial")])
    client = _GateClient()
    assert await pass_subscribe_gate(client, SimpleNamespace(username="b"), msg) is False
    assert msg.clicked is None


@pytest.mark.asyncio
async def test_private_invite_links_are_skipped(monkeypatch) -> None:
    """+hash invite links need importChatInvite; skip rather than guess."""
    joined: list = []

    async def _fake_mute(_c, peer):
        joined.append(peer)
        return True

    monkeypatch.setattr(_verify, "mute_peer", _fake_mute)
    msg = _GateMsg([
        _UrlBtn("channel", "https://t.me/+AbCdEf123"),
        _CbBtn("✅ Я подписался", data=b"check_join"),
    ])
    await pass_subscribe_gate(_GateClient(), SimpleNamespace(username="b"), msg)
    assert joined == []  # nothing joined, but the confirm click still happened
