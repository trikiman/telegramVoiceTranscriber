"""Core logic + discovery sources for the Active VPN Bot Harvester (v1.3).

The harvester actively hunts VPN-trial bots (rather than passively waiting for
sponsored ads like the v1.2 finder) and files qualifying bots into the target
folder until a quota is met, guaranteeing at least one long (30-day) trial.

This module is split deliberately:

* **Pure core** (no Telegram/network imports at module load):
  :func:`extract_bot_links`, :class:`Candidate`, :class:`HarvestState`,
  :class:`FloodBreaker`. These are fully unit-tested.

* **Discovery sources** (lazy ``telethon`` imports inside each function):
  :func:`iter_global_message_matches`, :func:`iter_channel_feed_matches`,
  :func:`iter_sponsored_matches`. Each is an async generator yielding
  :class:`Candidate` objects and handles ``FloodWaitError`` via a shared
  :class:`FloodBreaker` so a rate-limit storm degrades gracefully instead of
  crashing the run (a real cause of "3 days, no results").
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Pure, testable core
# ---------------------------------------------------------------------------

# Bot deep-links in free text: t.me/foo_bot, @foo_bot, https://t.me/foo_bot?start=TOKEN
# Requiring the "bot" suffix keeps false positives (channels, users) out — the
# same convention discover-channels.py / probe-bots.py use.
BOT_LINK_REGEX = re.compile(
    r"(?:https?://)?(?:t\.me/|@)([a-zA-Z0-9_]{3,60}bot)(?:\?start=([a-zA-Z0-9_-]+))?",
    re.IGNORECASE,
)

# Global message-search queries. 30-day queries come FIRST so the required
# long trial tends to be found before the quota fills with shorter ones.
VPN_MESSAGE_QUERIES: tuple[str, ...] = (
    "vpn 30 дней бесплатно",
    "впн 30 дней бесплатно",
    "vpn месяц бесплатно",
    "vpn free 30 days bot",
    "бесплатный vpn бот",
    "впн бесплатно бот",
    "vpn бесплатно",
    "vpn пробный период бот",
)

# Keyword terms for public channel/bot search (contacts.Search) and for
# prioritising which subscribed channels to scan first.
VPN_SEARCH_TERMS: tuple[str, ...] = (
    "vpn", "впн", "free vpn", "vpn бесплатно", "vpn trial", "прокси vpn",
)

_VPN_TITLE_MARKERS: tuple[str, ...] = (
    "vpn", "впн", "proxy", "прокси", "vless", "mtproto", "обход", "unblock",
)


def looks_vpnish(title: str | None, username: str | None) -> bool:
    """Heuristic: does this channel/bot title or username look VPN-related?"""
    hay = f"{title or ''} {username or ''}".lower()
    return any(m in hay for m in _VPN_TITLE_MARKERS)


def extract_bot_links(text: str) -> list[tuple[str, str | None]]:
    """Extract (bot_username_lower, start_token) pairs from free text.

    Usernames are lower-cased and de-duplicated preserving first-seen order.
    The first ``?start=`` token seen for a username wins.
    """
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for match in BOT_LINK_REGEX.finditer(text or ""):
        username = match.group(1).lower()
        token = match.group(2) or None
        if username in seen:
            continue
        seen.add(username)
        out.append((username, token))
    return out


@dataclass
class Candidate:
    """A potential VPN bot discovered from some source, ready for judging."""

    username: str  # lower-cased, no leading @
    start_token: str | None
    text: str  # offer text to feed the judge
    source: str  # "global-search" | "channel-feed" | "sponsored"
    url: str | None = None  # deep-link (real for ads, reconstructed otherwise)
    button_text: str | None = None
    channel_id: int = 0
    message_id: int | None = None

    def build_url(self) -> str:
        """Return a deep-link URL for parse_tme_target (real or reconstructed)."""
        if self.url:
            return self.url
        base = f"https://t.me/{self.username}"
        return f"{base}?start={self.start_token}" if self.start_token else base


@dataclass
class HarvestState:
    """Tracks progress + stop conditions for one harvester run (pure logic)."""

    target_count: int = 5
    require_30_day: bool = True
    long_trial_days: int = 30
    max_start_attempts: int = 15

    bots_added: int = 0
    has_long_trial: bool = False
    start_attempts: int = 0
    seen_bots: set[str] = field(default_factory=set)
    filed_bots: set[str] = field(default_factory=set)

    def already_seen(self, username: str) -> bool:
        return username.lstrip("@").lower() in self.seen_bots

    def mark_seen(self, username: str) -> None:
        self.seen_bots.add(username.lstrip("@").lower())

    def record_start_attempt(self) -> None:
        self.start_attempts += 1

    def record_filed(self, username: str, trial_days: int | None) -> None:
        self.filed_bots.add(username.lstrip("@").lower())
        self.bots_added += 1
        if trial_days is not None and trial_days >= self.long_trial_days:
            self.has_long_trial = True

    def needs_only_long_trial(self) -> bool:
        """Quota met but the required long trial is still missing."""
        return (
            self.bots_added >= self.target_count
            and self.require_30_day
            and not self.has_long_trial
        )

    def should_file(self, trial_days: int | None) -> bool:
        """Given current quota state, is this qualifying offer worth filing?

        Under quota → file any good offer. Once quota is met but we still owe a
        long trial → only file offers that provide that long trial.
        """
        if self.needs_only_long_trial():
            return trial_days is not None and trial_days >= self.long_trial_days
        return self.bots_added < self.target_count

    def should_stop(self) -> bool:
        """True once the quota is met and the long-trial requirement satisfied."""
        quota_met = self.bots_added >= self.target_count
        long_ok = self.has_long_trial or not self.require_30_day
        return quota_met and long_ok

    def budget_exhausted(self) -> bool:
        """True once we've spent the /start attempt budget (ban-safety cap)."""
        return self.start_attempts >= self.max_start_attempts


@dataclass
class FloodBreaker:
    """Circuit breaker for Telegram FloodWait storms.

    Trips (stop making requests) when any single wait is very long, when too
    many flood events occur, or when cumulative forced waiting is excessive.
    """

    single_trip_s: int = 300
    max_events: int = 5
    cumulative_trip_s: int = 600

    events: int = 0
    total_wait_s: int = 0
    _tripped: bool = False

    def record(self, seconds: int) -> None:
        secs = max(0, int(seconds))
        self.events += 1
        self.total_wait_s += secs
        if secs >= self.single_trip_s:
            self._tripped = True
        if self.events >= self.max_events:
            self._tripped = True
        if self.total_wait_s >= self.cumulative_trip_s:
            self._tripped = True

    @property
    def tripped(self) -> bool:
        return self._tripped


# ---------------------------------------------------------------------------
# Discovery sources (async generators; telethon imported lazily)
# ---------------------------------------------------------------------------


async def iter_global_message_matches(
    client,
    queries: tuple[str, ...] = VPN_MESSAGE_QUERIES,
    per_query_limit: int = 40,
) -> AsyncIterator[Candidate]:
    """Yield bot candidates from Telegram global message search.

    NOTE: Telegram "global search" is not a web-wide index — it primarily
    returns messages from chats/channels the account already follows plus a
    limited public set. Subscribe to VPN channels (scripts/subscribe-channels)
    to widen this source materially.
    """
    import asyncio
    import random

    from telethon.errors import FloodWaitError

    breaker = _get_breaker(client)
    for query in queries:
        if breaker.tripped:
            log.warning("harvest_breaker_tripped", stage="global-search")
            return
        try:
            async for msg in client.iter_messages(None, search=query, limit=per_query_limit):
                text = msg.text or ""
                if len(text) < 20:
                    continue
                for username, token in extract_bot_links(text):
                    yield Candidate(
                        username=username,
                        start_token=token,
                        text=text[:1500],
                        source="global-search",
                        channel_id=getattr(msg, "chat_id", 0) or 0,
                        message_id=getattr(msg, "id", None),
                    )
        except FloodWaitError as exc:
            breaker.record(getattr(exc, "seconds", 0))
            log.warning("global_search_floodwait", query=query, seconds=exc.seconds)
            if breaker.tripped:
                return
            await asyncio.sleep(min(int(exc.seconds), 30))
        except Exception as exc:  # noqa: BLE001 — one bad query shouldn't kill the run
            log.warning("global_search_failed", query=query, error=str(exc))
        await asyncio.sleep(random.uniform(1.0, 2.5))


async def iter_channel_feed_matches(
    client,
    max_channels: int = 40,
    per_channel_limit: int = 40,
) -> AsyncIterator[Candidate]:
    """Yield bot candidates by scanning subscribed broadcast-channel feeds.

    VPN-titled channels are scanned first so the LLM/rate budget is spent where
    hits are most likely.
    """
    import asyncio
    import random

    from telethon.errors import FloodWaitError

    breaker = _get_breaker(client)

    channels = []
    try:
        async for dialog in client.iter_dialogs():
            if dialog.is_channel and not dialog.is_group:
                channels.append(dialog)
    except Exception as exc:  # noqa: BLE001
        log.warning("channel_feed_list_failed", error=str(exc))
        return

    channels.sort(
        key=lambda d: 0
        if looks_vpnish(getattr(d, "title", ""), getattr(getattr(d, "entity", None), "username", None))
        else 1
    )

    scanned = 0
    for dialog in channels:
        if scanned >= max_channels or breaker.tripped:
            break
        scanned += 1
        try:
            async for msg in client.iter_messages(dialog.id, limit=per_channel_limit):
                text = msg.text or ""
                if len(text) < 20:
                    continue
                for username, token in extract_bot_links(text):
                    yield Candidate(
                        username=username,
                        start_token=token,
                        text=text[:1500],
                        source="channel-feed",
                        channel_id=dialog.id,
                        message_id=getattr(msg, "id", None),
                    )
        except FloodWaitError as exc:
            breaker.record(getattr(exc, "seconds", 0))
            log.warning("channel_feed_floodwait", channel=dialog.id, seconds=exc.seconds)
            if breaker.tripped:
                return
            await asyncio.sleep(min(int(exc.seconds), 30))
        except Exception as exc:  # noqa: BLE001
            log.warning("channel_feed_failed", channel=dialog.id, error=str(exc))
        await asyncio.sleep(random.uniform(1.0, 2.5))


async def iter_sponsored_matches(
    client,
    max_channels: int = 40,
) -> AsyncIterator[Candidate]:
    """Yield bot candidates from sponsored ("proxy sponsor") ads on channels.

    Best-effort: for many userbot accounts the sponsored-messages API returns
    nothing (ads are an official-client / non-Premium feature), so this source
    supplements — never replaces — the search/feed sources.
    """
    import asyncio

    from telethon.errors import FloodWaitError

    from tg_voice_transcriber.finder.links import parse_tme_target
    from tg_voice_transcriber.finder.sponsored import SponsoredMessageFetcher

    breaker = _get_breaker(client)
    fetcher = SponsoredMessageFetcher(client)

    channels = []
    try:
        async for dialog in client.iter_dialogs():
            if dialog.is_channel and not dialog.is_group:
                channels.append(dialog)
    except Exception as exc:  # noqa: BLE001
        log.warning("sponsored_list_failed", error=str(exc))
        return

    scanned = 0
    for dialog in channels:
        if scanned >= max_channels or breaker.tripped:
            break
        scanned += 1
        try:
            ads = await fetcher.fetch_sponsored_messages(dialog.id)
        except FloodWaitError as exc:
            breaker.record(getattr(exc, "seconds", 0))
            if breaker.tripped:
                return
            await asyncio.sleep(min(int(exc.seconds), 30))
            continue
        except Exception as exc:  # noqa: BLE001
            log.debug("sponsored_fetch_failed", channel=dialog.id, error=str(exc))
            continue

        for ad in ads:
            message = ad.get("message") or ""
            title = ad.get("title")
            judge_text = f"{title}\n{message}".strip() if title else message
            url = ad.get("url")
            button_text = ad.get("button_text")

            link = parse_tme_target(url, button_text=button_text)
            if link is not None and link.is_bot:
                yield Candidate(
                    username=link.username.lower(),
                    start_token=link.start_param,
                    text=judge_text[:1500],
                    source="sponsored",
                    url=url,
                    button_text=button_text,
                    channel_id=dialog.id,
                )
            else:
                # Ad target isn't a bot link — try extracting one from the copy.
                for username, token in extract_bot_links(judge_text):
                    yield Candidate(
                        username=username,
                        start_token=token,
                        text=judge_text[:1500],
                        source="sponsored",
                        channel_id=dialog.id,
                    )
        await asyncio.sleep(2.0)  # sponsored API is rate-limited


def _get_breaker(client) -> FloodBreaker:
    """Attach one shared FloodBreaker per client so all sources share a budget."""
    breaker = getattr(client, "_harvest_breaker", None)
    if not isinstance(breaker, FloodBreaker):
        breaker = FloodBreaker()
        with contextlib.suppress(Exception):  # pragma: no cover
            client._harvest_breaker = breaker
    return breaker
