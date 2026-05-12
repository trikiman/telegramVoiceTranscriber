"""Entry point: ``python -m tg_voice_transcriber``.

Connects to Telegram using the saved session, logs the account identity,
and idles until interrupted. Later phases will register event handlers
before the idle loop.
"""

from __future__ import annotations

import asyncio
import sys

import structlog

from tg_voice_transcriber.client import SessionInvalidError, TelegramUserbot
from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.logging import configure_logging

log = structlog.get_logger()

# Exit code 78 = EX_CONFIG (BSD sysexits) — "configuration error".
# Signals to systemd that the service cannot start without human intervention.
EX_CONFIG = 78


async def main() -> None:
    """Connect, log identity, idle until disconnected."""
    cfg = get_config()
    configure_logging(cfg.log_level)

    userbot = TelegramUserbot(cfg)

    try:
        await userbot.start()
    except SessionInvalidError as exc:
        log.error(
            "auth_required",
            reason=exc.reason,
            hint="Run `python scripts/login.py` on your local machine to create a valid session.",
        )
        sys.exit(EX_CONFIG)

    try:
        identity = await userbot.who_am_i()
        log.info("ready", connected_as=f"@{identity}")
        await userbot.client.run_until_disconnected()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutting_down", reason="interrupted")
    finally:
        await userbot.stop()


def _sync_main() -> None:
    """Synchronous wrapper for use as a console_scripts entry point."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    _sync_main()
