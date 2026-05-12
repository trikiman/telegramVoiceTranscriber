"""Telegram event handlers: filter voice notes in 1-on-1 chats and enqueue jobs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeAudio

from tg_voice_transcriber.queue import Job

log = structlog.get_logger()


def register_handlers(
    client: TelegramClient,
    queue: asyncio.Queue[Job],
    *,
    max_age_seconds: float = 600.0,
) -> None:
    """Register the voice-note event handler on the Telethon client.

    Args:
        client: The connected TelegramClient.
        queue: The bounded job queue.
        max_age_seconds: Skip voice notes older than this (default 10 min).
    """

    @client.on(events.NewMessage(incoming=True))
    @client.on(events.NewMessage(outgoing=True))
    async def on_voice_message(event: events.NewMessage.Event) -> None:
        """Filter and enqueue voice notes from 1-on-1 chats."""
        # Only 1-on-1 (private) chats
        if not event.is_private:
            return

        # Must have a voice note
        if not event.message.voice:
            return

        # Extract duration from audio attributes
        duration_s = _get_voice_duration(event.message)
        if duration_s is None:
            return

        # Skip old messages (e.g. backlog after reconnect)
        msg_date = event.message.date
        if msg_date is not None:
            now = datetime.now(timezone.utc)
            age_s = (now - msg_date).total_seconds()
            if age_s > max_age_seconds:
                log.debug("voice_skipped_old", msg_id=event.message.id, age_s=age_s)
                return

        # Determine direction
        direction = "outgoing" if event.message.out else "incoming"

        # Build job
        job = Job(
            chat_id=event.chat_id,
            msg_id=event.message.id,
            sender_id=event.message.sender_id or 0,
            direction=direction,
            voice_duration_s=duration_s,
        )

        # Enqueue (non-blocking)
        try:
            queue.put_nowait(job)
            log.info(
                "voice_enqueued",
                msg_id=job.msg_id,
                direction=direction,
                duration_s=duration_s,
            )
        except asyncio.QueueFull:
            log.warning("queue_full_dropped", msg_id=job.msg_id, direction=direction)


def _get_voice_duration(message) -> float | None:
    """Extract voice-note duration from message attributes."""
    if message.document is None:
        return None

    for attr in message.document.attributes:
        if isinstance(attr, DocumentAttributeAudio) and attr.voice:
            return float(attr.duration)

    return None
