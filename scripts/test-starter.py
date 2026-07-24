#!/usr/bin/env python3
"""Test script: verify starter.py can /start and mute a bot.

Usage:
    python scripts/test-starter.py @botusername

This sends /start to ONE bot and mutes it. Use a test bot you control or
a harmless public bot (e.g. @BotFather) to verify the plumbing works before
the finder runs at scale.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.starter import BotStarter
from tg_voice_transcriber.logging import configure_logging


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/test-starter.py @botusername", file=sys.stderr)
        sys.exit(1)

    bot_username = sys.argv[1]

    cfg = get_config()
    configure_logging("DEBUG")

    if not cfg.finder_phone:
        print("✗ TG_VOICE_FINDER_PHONE not set in .env", file=sys.stderr)
        sys.exit(1)

    if not cfg.finder_session_path.exists():
        print(
            f"✗ Finder session not found: {cfg.finder_session_path}\n"
            "  Run: python scripts/login-finder.py",
            file=sys.stderr,
        )
        sys.exit(1)

    client = TelegramClient(
        str(cfg.finder_session_path),
        cfg.api_id,
        cfg.api_hash.get_secret_value(),
    )

    await client.connect()

    if not await client.is_user_authorized():
        print("✗ Finder session invalid or expired", file=sys.stderr)
        await client.disconnect()
        sys.exit(1)

    me = await client.get_me()
    print(f"Connected as: {me.first_name} (@{me.username or 'none'})")
    print(f"Testing /start + mute with: {bot_username}")
    print()

    starter = BotStarter(client, max_starts_per_hour=10)

    success = await starter.start_and_mute(bot_username)

    stats = starter.stats()
    print()
    print(f"Rate limiter: {stats['starts_this_hour']}/{stats['max_per_hour']} used this hour")
    print(f"Slots remaining: {stats['slots_remaining']}")

    await client.disconnect()

    if success:
        print("\n✓ Test passed — bot started and muted")
        print("  Check the bot chat in Telegram to verify /start was sent")
        print("  and notifications are muted.")
    else:
        print("\n✗ Test failed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
    except Exception as exc:
        print(f"\n✗ Test failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
