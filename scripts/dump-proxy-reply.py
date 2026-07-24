#!/usr/bin/env python3
"""Press @TProxyRobot's 'Получить прокси' reply-keyboard button (send as text)
and dump what the bot replies, twice, to learn the proxy format.

Assumes the gate is already passed (run dump-bot-gate.py first).

Usage:
    python scripts/dump-proxy-reply.py @TProxyRobot
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient

from tg_voice_transcriber.config import get_config


async def _dump_latest(client, bot, tag: str) -> None:
    msgs = await client.get_messages(bot, limit=3)
    print(f"\n########## {tag} ##########")
    for i, m in enumerate(msgs):
        print(f"\n===== MSG[{i}] id={m.id} =====")
        print("TEXT:", repr(m.message))
        ents = getattr(m, "entities", None)
        if ents:
            for e in ents:
                url = getattr(e, "url", None)
                if url:
                    print("  ENTITY url:", url)
        markup = getattr(m, "reply_markup", None)
        if markup:
            for r, row in enumerate(getattr(markup, "rows", []) or []):
                for b, btn in enumerate(getattr(row, "buttons", []) or []):
                    print(
                        f"  BTN[{r}.{b}] text={getattr(btn,'text',None)!r} "
                        f"url={getattr(btn,'url',None)!r} "
                        f"type={type(btn).__name__}"
                    )


async def main() -> None:
    bot_username = (sys.argv[1] if len(sys.argv) > 1 else "TProxyRobot").lstrip("@")
    cfg = get_config()
    client = TelegramClient(
        str(cfg.finder_session_path),
        cfg.api_id,
        cfg.api_hash.get_secret_value(),
    )
    await client.connect()

    bot = await client.get_entity(bot_username)

    await client.send_message(bot, "Получить прокси")
    await asyncio.sleep(4.0)
    await _dump_latest(client, bot, "AFTER 1st 'Получить прокси'")

    await client.send_message(bot, "Получить прокси")
    await asyncio.sleep(4.0)
    await _dump_latest(client, bot, "AFTER 2nd 'Получить прокси'")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
