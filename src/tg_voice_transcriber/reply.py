"""Reply service: send and edit messages in Telegram with error handling."""

from __future__ import annotations

import structlog
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    MessageIdInvalidError,
    MessageNotModifiedError,
)

log = structlog.get_logger()


class ReplyService:
    """Handles sending and editing reply messages with retry logic.

    Catches FloodWaitError (sleeps), MessageIdInvalidError (swallows),
    and MessageNotModifiedError (swallows).
    """

    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    async def send_reply(self, chat_id: int, text: str, reply_to: int) -> int | None:
        """Send a text reply to a specific message.

        Args:
            chat_id: The chat to send in.
            text: Message text (plain, no parse_mode).
            reply_to: Message ID to reply to.

        Returns:
            The sent message ID, or None if sending failed.
        """
        import asyncio

        try:
            msg = await self._client.send_message(
                chat_id,
                text,
                reply_to=reply_to,
            )
            return msg.id
        except FloodWaitError as exc:
            log.warning("flood_wait_send", seconds=exc.seconds, chat_id=chat_id)
            await asyncio.sleep(exc.seconds)
            # Retry once after flood wait
            try:
                msg = await self._client.send_message(chat_id, text, reply_to=reply_to)
                return msg.id
            except Exception:
                log.error("send_retry_failed", chat_id=chat_id)
                return None
        except Exception:
            log.error("send_failed", chat_id=chat_id, exc_info=True)
            return None

    async def edit_message(self, chat_id: int, msg_id: int, text: str) -> bool:
        """Edit an existing message.

        Args:
            chat_id: The chat containing the message.
            msg_id: The message to edit.
            text: New text content.

        Returns:
            True if edit succeeded, False otherwise.
        """
        import asyncio

        try:
            await self._client.edit_message(chat_id, msg_id, text)
            return True
        except MessageIdInvalidError:
            # User deleted the message before we could edit — swallow
            log.debug("edit_msg_deleted", chat_id=chat_id, msg_id=msg_id)
            return False
        except MessageNotModifiedError:
            # Text is the same — no-op
            return True
        except FloodWaitError as exc:
            log.warning("flood_wait_edit", seconds=exc.seconds)
            await asyncio.sleep(exc.seconds)
            try:
                await self._client.edit_message(chat_id, msg_id, text)
                return True
            except (MessageIdInvalidError, MessageNotModifiedError):
                return False
            except Exception:
                log.error("edit_retry_failed", chat_id=chat_id, msg_id=msg_id)
                return False
        except Exception:
            log.error("edit_failed", chat_id=chat_id, msg_id=msg_id, exc_info=True)
            return False
