"""Telethon client wrapper with session-invalid detection."""

from __future__ import annotations

import structlog
from telethon import TelegramClient
from telethon.errors import AuthKeyError, UserDeactivatedBanError

from tg_voice_transcriber.config import Config

log = structlog.get_logger()


class SessionInvalidError(RuntimeError):
    """Raised when the Telegram session is invalid or expired.

    The ``reason`` attribute contains a short machine-readable tag
    (e.g. ``"not_authorized"``, ``"AuthKeyError"``).
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Session invalid: {reason}")


class TelegramUserbot:
    """Thin wrapper around :class:`TelegramClient`.

    Responsibilities:
    - Construct the client from :class:`Config`
    - Detect invalid/expired sessions and raise :class:`SessionInvalidError`
    - Expose ``start()`` / ``stop()`` / ``who_am_i()``
    - Expose the underlying ``client`` property for later phases to attach handlers

    Does NOT register any event handlers — that's Phase 4.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

        # Ensure the session file's parent directory exists
        config.session_path.parent.mkdir(parents=True, exist_ok=True)

        self._client = TelegramClient(
            session=str(config.session_path),
            api_id=config.api_id,
            api_hash=config.api_hash.get_secret_value(),
        )

    @property
    def client(self) -> TelegramClient:
        """Access the underlying TelegramClient (for event handler registration in later phases)."""
        return self._client

    async def start(self) -> None:
        """Connect to Telegram and verify the session is authorized.

        Raises:
            SessionInvalidError: if the session file is missing, expired, or revoked.
        """
        try:
            await self._client.connect()
        except (AuthKeyError, OSError) as exc:
            raise SessionInvalidError(reason=type(exc).__name__) from exc

        if not await self._client.is_user_authorized():
            await self._client.disconnect()
            raise SessionInvalidError(reason="not_authorized")

        log.info("telegram_connected", user=await self.who_am_i())

    async def stop(self) -> None:
        """Disconnect from Telegram cleanly."""
        if self._client.is_connected():
            await self._client.disconnect()
            log.info("telegram_disconnected")

    async def who_am_i(self) -> str:
        """Return a human-readable identifier for the logged-in account."""
        me = await self._client.get_me()
        if me is None:
            return "unknown"
        return me.username or me.first_name or str(me.id)
