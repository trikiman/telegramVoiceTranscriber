#!/usr/bin/env python3
"""Pass @TProxyRobot's subscribe-gate, then dump the post-gate state.

Joins the gate channels (muted, finder account), clicks the check_join
callback, then prints the resulting message + buttons so we can learn the
proxy-delivery format.

Usage:
    python scripts/dump-bot-gate.py @TProxyRobot
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.contacts import UnblockRequest

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.mute import mute_peer

GATE_CHANNELS = ["TProxyRU", "ProxyMTProto", "Blyat_Net"]


async def main() -> None:
    bot_username = (sys.argv[1] if len(sys.argv) > 1 else "TProxyRobot").lstrip("@")

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

    # Join + mute the gate channels (user rule: always mute).
    for ch in GATE_CHANNELS:
        try:
            ent = await client.get_entity(ch)
            await client(JoinChannelRequest(ent))
            await mute_peer(client, ent)
            print(f"joined+muted @{ch}")
        except Exception as exc:
            print(f"gate join @{ch} failed: {exc}")
        await asyncio.sleep(3.0)

    bot = await client.get_entity(bot_username)
    try:
        await client(UnblockRequest(id=bot))
    except Exception:
        pass

    await client.send_message(bot, "/start")
    await asyncio.sleep(3.0)

    # Click the "check_join" callback.
    msgs = await client.get_messages(bot, limit=3)
    clicked = False
    for m in msgs:
        markup = getattr(m, "reply_markup", None)
        if not markup:
            continue
        for row in getattr(markup, "rows", []) or []:
            for btn in getattr(row, "buttons", []) or []:
                if getattr(btn, "data", None) == b"check_join":
                    print("clicking check_join ...")
                    await m.click(data=b"check_join")
                    clicked = True
                    break
            if clicked:
                break
        if clicked:
            break

    if not clicked:
        print("check_join button not found")

    await asyncio.sleep(4.0)

    print("\n########## POST-GATE STATE ##########")
    msgs = await client.get_messages(bot, limit=6)
    for i, m in enumerate(msgs):
        print(f"\n===== MSG[{i}] id={m.id} =====")
        print("TEXT:", repr(m.message))
        markup = getattr(m, "reply_markup", None)
        if markup is None:
            print("(no buttons)")
            continue
        print("BUTTONS:")
        for r, row in enumerate(getattr(markup, "rows", []) or []):
            for b, btn in enumerate(getattr(row, "buttons", []) or []):
                print(
                    f"  [{r}.{b}] text={getattr(btn,'text',None)!r} "
                    f"url={getattr(btn,'url',None)!r} "
                    f"data={getattr(btn,'data',None)!r} "
                    f"type={type(btn).__name__}"
                )

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
