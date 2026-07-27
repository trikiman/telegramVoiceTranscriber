"""Live bot verification: read a bot's REAL /start welcome screen.

Ad copy, search-result snippets, and channel posts are unreliable marketing
text — live-verification found bots claiming "30 days free" that turned out
to be 24 hours, 2 days, or gated behind submitting a review screenshot once
actually opened (see the harvester Phase-10 fix). The only trustworthy source
of an offer's real terms is the bot's own live welcome screen after /start.

This module extracts and hardens the technique first built ad-hoc in
scripts/probe-bots.py. The original had a real bug: it read the first
non-empty message in the dialog without filtering out OUTGOING messages, so
a slow-to-reply bot could cause it to misread the /start command we just sent
as if it were the bot's reply.
"""

from __future__ import annotations

import asyncio
import contextlib
import random

import structlog
from telethon import TelegramClient

from tg_voice_transcriber.finder.mute import mute_peer

log = structlog.get_logger()

# Labels on the "I subscribed / continue" button a gate bot shows AFTER you
# join its required channels. Multilingual on purpose: gates appear in
# Russian, English and Persian (fa) bots alike, and a Russian-only list meant
# non-Russian gates were never even recognised as gates.
_JOINED_BUTTON_MARKERS: tuple[str, ...] = (
    # ru
    "я подписался", "подписался", "проверить", "готово", "продолжить",
    # en
    "i subscribed", "i joined", "check", "continue", "done",
    # fa (عضو شدم = "I joined")
    "عضو شدم", "بررسی", "ادامه",
)

# Callback payloads gate bots conventionally use for that button.
_JOINED_CALLBACK_MARKERS: tuple[bytes, ...] = (
    b"check_join", b"checkjoin", b"check_sub", b"checksub",
    b"subscribed", b"joined", b"check", b"verify",
)


def _is_joined_button(btn) -> bool:
    """True if this button is the 'I've subscribed, let me in' confirmation."""
    label = (getattr(btn, "text", "") or "").strip().lower()
    if any(m in label for m in _JOINED_BUTTON_MARKERS):
        return True
    data = getattr(btn, "data", None)
    return bool(data and any(m in data.lower() for m in _JOINED_CALLBACK_MARKERS))


def _gate_channel_urls(msg) -> list[str]:
    """Return t.me channel links a gate message wants us to join.

    Excludes the bot's own deep links (?start=…) and proxy links — we only
    want the channels being demanded as an entry condition.
    """
    urls: list[str] = []
    markup = getattr(msg, "reply_markup", None)
    if not markup:
        return urls
    for row in getattr(markup, "rows", []) or []:
        for btn in getattr(row, "buttons", []) or []:
            url = getattr(btn, "url", None)
            if not url or "t.me/" not in url:
                continue
            if "?start=" in url or "proxy" in url.lower():
                continue
            urls.append(url)
    return urls


async def pass_subscribe_gate(client: TelegramClient, bot_entity, message) -> bool:
    """Join the channels a gate bot demands, MUTE them, then confirm.

    Some bots refuse to show their offer until you join a channel or two. That
    is a legitimate (if annoying) condition, and refusing to pass it means the
    real terms stay unknown and the bot is rejected unseen. So we join — but
    every joined channel is muted immediately, per the standing rule that
    nothing this account touches may generate notifications.

    Returns True if a gate was detected and we clicked through it, so the
    caller knows to re-read the screen.
    """
    from telethon.tl.functions.channels import JoinChannelRequest

    markup = getattr(message, "reply_markup", None)
    if not markup:
        return False

    confirm_btn = None
    for row in getattr(markup, "rows", []) or []:
        for btn in getattr(row, "buttons", []) or []:
            if _is_joined_button(btn):
                confirm_btn = btn
                break
        if confirm_btn:
            break
    if confirm_btn is None:
        return False

    for url in _gate_channel_urls(message):
        uname = url.rstrip("/").split("/")[-1].lstrip("@")
        if not uname or uname.startswith("+"):
            # Private invite links need importChatInvite; skip rather than
            # guess, the confirm click below may still succeed.
            continue
        try:
            channel = await client.get_entity(uname)
            await client(JoinChannelRequest(channel))
            await mute_peer(client, channel)   # mandatory: never notify
            log.info("gate_channel_joined_muted", channel=uname)
        except Exception as exc:  # noqa: BLE001
            log.debug("gate_channel_join_failed", channel=uname, error=str(exc))
        await asyncio.sleep(random.uniform(1.5, 3.0))

    with contextlib.suppress(Exception):
        data = getattr(confirm_btn, "data", None)
        if data:
            await message.click(data=data)
        else:
            await message.click(text=getattr(confirm_btn, "text", None))
        log.info("gate_confirm_clicked", bot=getattr(bot_entity, "username", None))
        return True
    return False


def _collect_buttons(msg) -> list[str]:
    """Return the text labels of every inline/reply button on a message."""
    labels: list[str] = []
    markup = getattr(msg, "reply_markup", None)
    if not markup:
        return labels
    for row in getattr(markup, "rows", []) or []:
        for btn in getattr(row, "buttons", []) or []:
            text = getattr(btn, "text", None)
            if text:
                labels.append(text)
    return labels


async def fetch_bot_welcome(
    client: TelegramClient,
    bot_entity,
    wait_s_range: tuple[float, float] = (3.0, 5.0),
    limit: int = 5,
    max_wait_s: float = 20.0,
    poll_interval_s: float = 2.0,
    pass_gate: bool = True,
) -> tuple[str, list[str]]:
    """Return the bot's live welcome text + button labels after /start.

    Assumes the caller has ALREADY sent /start (or /start <token>) to
    ``bot_entity`` — this function only waits, then reads back the reply.

    Polls until a reply arrives or ``max_wait_s`` elapses, rather than reading
    once after a fixed sleep: a "no reply" result makes the caller REJECT the
    bot, so being impatient here silently discards genuinely good bots that
    happen to answer slowly (LLM-backed or queued bots routinely take >5s).

    When ``pass_gate`` is set and the first reply is a subscribe-gate ("join
    this channel to continue"), the demanded channels are joined AND MUTED and
    the confirmation button is clicked, then the real screen is re-read. Bots
    behind a gate would otherwise be rejected without their offer ever being
    seen.

    Returns ``("", [])`` if no incoming (non-outgoing) text message arrives
    within ``max_wait_s``. Callers MUST treat an empty result as
    reject-worthy — never fall back to trusting ad/search-snippet text, since
    that defeats the entire purpose of live verification.
    """
    # First wait keeps the human-like pacing the rate-limit story depends on;
    # subsequent polls are short since we're only re-reading an existing chat.
    await asyncio.sleep(random.uniform(*wait_s_range))
    deadline = asyncio.get_running_loop().time() + max_wait_s
    gate_attempted = False

    while True:
        messages = await client.get_messages(bot_entity, limit=limit)
        welcome = next(
            (m for m in messages if not m.out and (m.message or "").strip()),
            None,
        )
        if welcome is not None:
            if pass_gate and not gate_attempted:
                gate_attempted = True
                if await pass_subscribe_gate(client, bot_entity, welcome):
                    # Gate cleared — the offer screen replaces the gate copy,
                    # so wait for it and read again rather than judging the
                    # "please subscribe" text as if it were the offer.
                    await asyncio.sleep(random.uniform(2.5, 4.0))
                    deadline = max(
                        deadline,
                        asyncio.get_running_loop().time() + max_wait_s / 2,
                    )
                    continue
            return welcome.message, _collect_buttons(welcome)

        if asyncio.get_running_loop().time() >= deadline:
            log.debug(
                "verify_no_incoming_reply",
                bot=getattr(bot_entity, "username", None),
                waited_s=max_wait_s,
            )
            return "", []

        await asyncio.sleep(poll_interval_s)
