"""Sponsored message fetcher with 5-minute cache per channel.

Telegram's sponsored messages API requires caching results for 5 minutes minimum.
We poll channels on a slow rotation to avoid rate limits.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from telethon import TelegramClient

try:
    # In Telethon 1.43+, sponsored messages live under functions.messages
    # (not functions.channels), and the request takes `peer`, not `channel`.
    from telethon.tl.functions.messages import GetSponsoredMessagesRequest
    from telethon.tl.types import SponsoredMessage
    _SPONSORED_AVAILABLE = True
except ImportError:
    _SPONSORED_AVAILABLE = False

log = structlog.get_logger()

CACHE_TTL_SECONDS = 300  # 5 minutes as required by Telegram API


class SponsoredMessageFetcher:
    """Fetches sponsored messages from channels with 5-min caching."""

    def __init__(self, client: TelegramClient) -> None:
        self._client = client
        self._cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}  # channel_id -> (timestamp, messages)

    async def fetch_sponsored_messages(self, channel_id: int) -> list[dict[str, Any]]:
        """Fetch sponsored messages for a channel, respecting the 5-min cache.

        Args:
            channel_id: Telegram channel id

        Returns:
            List of dicts with keys: message (text), sponsor_info (optional), url (optional)
        """
        now = time.time()

        # Check cache
        if channel_id in self._cache:
            cached_ts, cached_msgs = self._cache[channel_id]
            if now - cached_ts < CACHE_TTL_SECONDS:
                log.debug(
                    "sponsored_cache_hit",
                    channel_id=channel_id,
                    age_s=int(now - cached_ts),
                )
                return cached_msgs

        # Sponsored API not available in this Telethon build
        if not _SPONSORED_AVAILABLE:
            return []

        # Cache miss or expired — fetch from API
        try:
            peer = await self._client.get_input_entity(channel_id)
            result = await self._client(GetSponsoredMessagesRequest(peer=peer))
        except Exception as exc:
            log.error(
                "sponsored_fetch_failed",
                channel_id=channel_id,
                error=str(exc),
            )
            return []

        messages = []
        for msg in getattr(result, "messages", []):
            if not isinstance(msg, SponsoredMessage):
                continue

            # Extract text
            text = getattr(msg, "message", "")

            # Extract sponsor info (may be a plain string or an object)
            sponsor_info = getattr(msg, "sponsor_info", None)
            sponsor_title = None
            if isinstance(sponsor_info, str):
                sponsor_title = sponsor_info
            elif sponsor_info is not None:
                t = getattr(sponsor_info, "title", None)
                sponsor_title = t if isinstance(t, str) else None

            # Extract URL + button text (the deep-link target lives here)
            url = getattr(msg, "url", None)
            button_text = getattr(msg, "button_text", None)
            title = getattr(msg, "title", None)

            if text or sponsor_title or url:
                messages.append({
                    "message": text,
                    "sponsor_title": sponsor_title,
                    "title": title,
                    "button_text": button_text,
                    "url": url,
                })

        # Update cache
        self._cache[channel_id] = (now, messages)

        log.info(
            "sponsored_fetched",
            channel_id=channel_id,
            count=len(messages),
        )

        return messages

    def clear_cache(self, channel_id: int | None = None) -> None:
        """Clear cache for a specific channel or all channels."""
        if channel_id is not None:
            self._cache.pop(channel_id, None)
        else:
            self._cache.clear()
