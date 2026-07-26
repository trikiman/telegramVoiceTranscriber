#!/usr/bin/env python3
"""Add specific bot usernames to the target folder (finder account).

/start + mute each bot, then add to the pinned folder. Uses the same
clobber-proof add_peer_to_folder + resolve_folder helpers as the harvester.

Usage:
    python scripts/add-bots-to-folder.py vpn_chel_bot tanomivpnrobot
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from telethon import TelegramClient

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder import db as finder_db
from tg_voice_transcriber.finder.folder import add_peer_to_folder, resolve_folder
from tg_voice_transcriber.finder.mute import mute_peer


async def main() -> int:
    usernames = [a.lstrip("@") for a in sys.argv[1:]]
    if not usernames:
        print("Usage: add-bots-to-folder.py <bot> [bot ...]", file=sys.stderr)
        return 1

    cfg = get_config()
    client = TelegramClient(str(cfg.finder_session_path), cfg.api_id,
                            cfg.api_hash.get_secret_value())
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT AUTHORIZED")
        return 1

    await finder_db.init_finder_db(cfg.finder_db_path)
    db_cfg = await finder_db.load_finder_config(cfg.finder_db_path)
    folder = await resolve_folder(client, title=cfg.finder_folder_title,
                                  folder_id=db_cfg.get("target_folder_id"))
    if folder is None:
        print("Folder not found")
        return 1
    print(f"Folder id={folder.id}, {len(folder.include_peers)} peers before")

    for uname in usernames:
        try:
            ent = await client.get_entity(uname)
            await client.send_message(ent, "/start")
            await asyncio.sleep(2)
            await mute_peer(client, ent)
            added = await add_peer_to_folder(
                client, folder, peer_id=ent.id,
                access_hash=getattr(ent, "access_hash", None))
            print(f"  @{uname}: {'ADDED' if added else 'already present'}")
        except Exception as exc:
            print(f"  @{uname}: FAILED — {exc}")
        await asyncio.sleep(3)

    live = await resolve_folder(client, title=cfg.finder_folder_title, folder_id=folder.id)
    print(f"Folder now has {len(live.include_peers)} peers")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
