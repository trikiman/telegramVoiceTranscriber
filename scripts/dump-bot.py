#!/usr/bin/env python3
"""Dump a bot's latest messages + inline button labels (finder session).

Sends /start, waits, then prints the last few messages with their reply_markup
button rows so we can see exactly what labels/format a proxy bot uses.

Usage:
    python scripts/dump-bot.py @TProxyRobot
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient
from telethon.tl.functions.contacts import UnblockRequest

from tg_voice_transcriber.config import get_config


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: dump-bot.py @bot", file=sys.stderr)
        sys.exit(1)
    bot_username = sys.argv[1].lstrip("@")

    cfg = get_config()
    client = TelegramClient(
        str(cfg.finder_session_path),
        cfg.api_id,
        cfg.api_hash.get_secret_value(),
    )
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT AUTHORIZED", file=sys.stderr)
        sys.exit(1)

    bot = await client.get_entity(bot_username)
    try:
        await client(UnblockRequest(id=bot))
    except Exception:
        pass

    await client.send_message(bot, "/start")
    await asyncio.sleep(4.0)

    msgs = await client.get_messages(bot, limit=6)
    for i, m in enumerate(msgs):
        print(f"\n===== MSG[{i}] id={m.id} =====")
        print("TEXT:")
        print(repr(m.message))
        markup = getattr(m, "reply_markup", None)
        if markup is None:
            print("(no buttons)")
            continue
        print("BUTTONS:")
        for r, row in enumerate(getattr(markup, "rows", []) or []):
            for b, btn in enumerate(getattr(row, "buttons", []) or []):
                txt = getattr(btn, "text", None)
                url = getattr(btn, "url", None)
                data = getattr(btn, "data", None)
                print(f"  [{r}.{b}] text={txt!r} url={url!r} data={data!r} type={type(btn).__name__}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
