#!/usr/bin/env python3
"""Probe: dump the raw field structure of a live SponsoredMessage.

Read-only. Helps us learn how the ad points to its target bot/channel
so the judge can extract target_bot reliably.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient
from telethon.tl.functions.messages import GetSponsoredMessagesRequest
from telethon.tl.types import SponsoredMessage

from tg_voice_transcriber.config import get_config


async def main() -> None:
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

    dumped = 0
    async for dialog in client.iter_dialogs():
        if not (dialog.is_channel and not dialog.is_group):
            continue
        try:
            peer = await client.get_input_entity(dialog.id)
            result = await client(GetSponsoredMessagesRequest(peer=peer))
        except Exception as exc:
            continue

        for msg in getattr(result, "messages", []):
            if not isinstance(msg, SponsoredMessage):
                continue
            print(f"\n===== ad in [{dialog.title[:30]}] =====")
            # Dump all public attributes
            for attr in dir(msg):
                if attr.startswith("_"):
                    continue
                try:
                    val = getattr(msg, attr)
                except Exception:
                    continue
                if callable(val):
                    continue
                sval = repr(val)
                if len(sval) > 300:
                    sval = sval[:300] + "...(truncated)"
                print(f"  {attr} = {sval}")
            dumped += 1
            if dumped >= 3:
                await client.disconnect()
                return
        await asyncio.sleep(2)

    print(f"\nDumped {dumped} sponsored message(s)")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
