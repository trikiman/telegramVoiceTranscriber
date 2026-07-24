"""Bot starter: send /start, mute, with rate limiting to avoid bans.

This is the ban-sensitive part of the finder. Auto-messaging many bots is exactly
the automation pattern Telegram bans userbots for. We rate-limit aggressively:
max N /start commands per hour, random delay between each, never in bursts.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

import structlog
from telethon import TelegramClient

from tg_voice_transcriber.finder.mute import mute_peer

log = structlog.get_logger()

# Rate limits (conservative defaults — adjust if experience shows they're safe)
MAX_STARTS_PER_HOUR = 10  # Max bots to /start per rolling hour
MIN_DELAY_SECONDS = 5  # Minimum delay between consecutive /start commands
MAX_DELAY_SECONDS = 30  # Maximum delay (adds randomness to look human)


@dataclass
class RateLimiter:
    """Rolling-window rate limiter for /start commands."""

    max_per_hour: int
    window_seconds: float = 3600.0

    def __post_init__(self) -> None:
        self._timestamps: list[float] = []

    def check_and_record(self) -> bool:
        """Check if a new /start is allowed. If yes, record it and return True."""
        now = time.time()
        cutoff = now - self.window_seconds

        # Drop timestamps outside the window
        self._timestamps = [ts for ts in self._timestamps if ts > cutoff]

        if len(self._timestamps) >= self.max_per_hour:
            log.warning(
                "rate_limit_hit",
                starts_this_hour=len(self._timestamps),
                max=self.max_per_hour,
            )
            return False

        self._timestamps.append(now)
        return True

    def next_available_in(self) -> float:
        """Return seconds until the next /start is allowed (0 if allowed now)."""
        now = time.time()
        cutoff = now - self.window_seconds
        self._timestamps = [ts for ts in self._timestamps if ts > cutoff]

        if len(self._timestamps) < self.max_per_hour:
            return 0.0

        # Oldest timestamp will fall out of the window first
        oldest = min(self._timestamps)
        return (oldest + self.window_seconds) - now


class BotStarter:
    """Sends /start to bots, mutes them, with rate limiting."""

    def __init__(
        self,
        client: TelegramClient,
        max_starts_per_hour: int = MAX_STARTS_PER_HOUR,
    ) -> None:
        self._client = client
        self._limiter = RateLimiter(max_per_hour=max_starts_per_hour)

    async def start_and_mute(
        self, bot_username: str, start_param: str | None = None
    ) -> bool:
        """Send /start to a bot, then mute it.

        Args:
            bot_username: The bot's @username (with or without @)
            start_param: Optional deep-link token. When present, sends
                `/start <token>` — this is the referral that CLAIMS the trial.
                Without it, a plain `/start` just opens the bot.

        Returns:
            True if successfully started and muted, False on error or rate limit.
        """
        username = bot_username.lstrip("@")

        # Rate limit check
        if not self._limiter.check_and_record():
            wait_s = self._limiter.next_available_in()
            log.warning(
                "start_rate_limited",
                bot=username,
                wait_seconds=int(wait_s),
            )
            return False

        # Random delay before /start (anti-pattern detection)
        delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
        log.debug("start_delay", bot=username, delay_s=round(delay, 1))
        await asyncio.sleep(delay)

        # Resolve the bot
        try:
            bot_entity = await self._client.get_entity(username)
        except Exception as exc:
            log.error("start_resolve_failed", bot=username, error=str(exc))
            return False

        # Send /start (with the referral token if we have one — that's what
        # actually claims the trial).
        start_text = f"/start {start_param}" if start_param else "/start"
        try:
            await self._client.send_message(bot_entity, start_text)
            log.info(
                "start_sent",
                bot=username,
                bot_id=bot_entity.id,
                with_token=bool(start_param),
            )
        except Exception as exc:
            log.error("start_send_failed", bot=username, error=str(exc))
            return False

        # Mute the bot (mandatory — no notification spam on the finder account)
        muted = await mute_peer(self._client, bot_entity)
        if not muted:
            # Non-fatal — the bot was started, just not muted
            return True

        return True

    def stats(self) -> dict[str, int]:
        """Return current rate limiter stats."""
        now = time.time()
        cutoff = now - self._limiter.window_seconds
        recent = [ts for ts in self._limiter._timestamps if ts > cutoff]

        return {
            "starts_this_hour": len(recent),
            "max_per_hour": self._limiter.max_per_hour,
            "slots_remaining": self._limiter.max_per_hour - len(recent),
        }
