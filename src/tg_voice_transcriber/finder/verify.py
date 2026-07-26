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
import random

import structlog
from telethon import TelegramClient

log = structlog.get_logger()


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
) -> tuple[str, list[str]]:
    """Return the bot's live welcome text + button labels after /start.

    Assumes the caller has ALREADY sent /start (or /start <token>) to
    ``bot_entity`` — this function only waits, then reads back the reply.

    Polls until a reply arrives or ``max_wait_s`` elapses, rather than reading
    once after a fixed sleep: a "no reply" result makes the caller REJECT the
    bot, so being impatient here silently discards genuinely good bots that
    happen to answer slowly (LLM-backed or queued bots routinely take >5s).

    Returns ``("", [])`` if no incoming (non-outgoing) text message arrives
    within ``max_wait_s``. Callers MUST treat an empty result as
    reject-worthy — never fall back to trusting ad/search-snippet text, since
    that defeats the entire purpose of live verification.
    """
    # First wait keeps the human-like pacing the rate-limit story depends on;
    # subsequent polls are short since we're only re-reading an existing chat.
    await asyncio.sleep(random.uniform(*wait_s_range))
    deadline = asyncio.get_running_loop().time() + max_wait_s

    while True:
        messages = await client.get_messages(bot_entity, limit=limit)
        welcome = next(
            (m for m in messages if not m.out and (m.message or "").strip()),
            None,
        )
        if welcome is not None:
            return welcome.message, _collect_buttons(welcome)

        if asyncio.get_running_loop().time() >= deadline:
            log.debug(
                "verify_no_incoming_reply",
                bot=getattr(bot_entity, "username", None),
                waited_s=max_wait_s,
            )
            return "", []

        await asyncio.sleep(poll_interval_s)
