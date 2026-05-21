"""Ingest handler: captures new posts in tracked channels into the buffer."""

from __future__ import annotations

from pathlib import Path

import structlog
from telethon import TelegramClient, events

from tg_voice_transcriber.digest import db as digest_db

log = structlog.get_logger()

# Minimum post length to consider (below this, usually just a reaction/emoji/media caption)
MIN_POST_LENGTH = 20


def register_digest_handler(
    client: TelegramClient,
    db_path: Path,
    *,
    default_track_all: bool = True,
) -> None:
    """Register the NewMessage handler for channel-post ingest.

    Args:
        client: the connected TelegramClient
        db_path: path to digest SQLite
        default_track_all: if True, auto-add unknown channels to tracked list on first post seen
    """

    @client.on(events.NewMessage(incoming=True))
    async def on_channel_post(event: events.NewMessage.Event) -> None:
        """Capture channel and (opt-in) supergroup posts matching the digest criteria.

        Default policy:
        - Broadcast channels: ingest everything; auto-add on first post if
          ``default_track_all=True``.
        - Supergroups (forum or regular): require explicit subscription via
          ``/digest sub <chat_id>``. Never auto-add — they're chatty and would
          blow up token spend.
        - DMs and basic groups: hard skip.
        """
        # Skip DMs and legacy basic groups (only allow Telegram "channel" entities,
        # which include both broadcast channels and supergroups).
        if not event.is_channel:
            return

        # Distinguish broadcast channel vs supergroup. Telethon sets
        # ``event.is_group=True`` for supergroups (and basic groups, but those
        # were already filtered above by ``is_channel``).
        is_supergroup = bool(event.is_group)

        message = event.message
        text = (message.message or "").strip()
        channel_id = event.chat_id

        # Log every post we see at DEBUG level for troubleshooting
        log.debug(
            "digest_channel_post_seen",
            channel_id_hashed=_hashed(channel_id),
            msg_id=message.id,
            text_length=len(text),
            has_media=message.media is not None,
            is_supergroup=is_supergroup,
        )

        # Skip media-only or very short posts
        if len(text) < MIN_POST_LENGTH:
            log.debug(
                "digest_post_skipped_short",
                channel_id_hashed=_hashed(channel_id),
                text_length=len(text),
            )
            return

        # Hard skip: user has explicitly unsubscribed (blocklist)
        if await digest_db.is_channel_blocked(db_path, channel_id):
            log.debug(
                "digest_post_skipped_blocked",
                channel_id_hashed=_hashed(channel_id),
                msg_id=message.id,
            )
            return

        # Check if tracked
        is_tracked = await digest_db.is_channel_tracked(db_path, channel_id)
        if not is_tracked:
            # Auto-track only broadcast channels. Supergroups always require
            # explicit /digest sub to avoid runaway noise from chatty groups.
            if default_track_all and not is_supergroup:
                chat = await event.get_chat()
                title = getattr(chat, "title", None) or getattr(chat, "username", None) or str(channel_id)
                username = getattr(chat, "username", None)
                added = await digest_db.add_tracked_channel(
                    db_path, channel_id, title, username
                )
                if added:
                    log.info(
                        "digest_channel_auto_added",
                        channel_id_hashed=_hashed(channel_id),
                        title=title,
                    )
            else:
                # Either default_track_all=False, or this is an untracked supergroup.
                # Skip until the user explicitly subscribes.
                if is_supergroup:
                    log.debug(
                        "digest_post_skipped_untracked_supergroup",
                        channel_id_hashed=_hashed(channel_id),
                        msg_id=message.id,
                    )
                return

        # Dedupe against recent posts
        post_hash = digest_db.compute_post_hash(text)
        seen = await digest_db.seen_recently(db_path, post_hash)
        if seen:
            log.debug(
                "digest_post_dedup_skipped",
                channel_id_hashed=_hashed(channel_id),
                msg_id=message.id,
            )
            return

        # Buffer the post
        buffered = await digest_db.buffer_post(
            db_path,
            channel_id=channel_id,
            message_id=message.id,
            text=text,
        )
        if buffered:
            log.info(
                "digest_post_buffered",
                channel_id_hashed=_hashed(channel_id),
                msg_id=message.id,
                text_length=len(text),
            )


def _hashed(value: int) -> str:
    """Short hashed representation for privacy-safe logs."""
    import hashlib
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]
