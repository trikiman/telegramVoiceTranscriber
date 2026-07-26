#!/usr/bin/env python3
"""Remove specific bot usernames from the target folder (finder account).

Mirrors add_peer_to_folder's clobber-proof pattern: re-fetch the live filter
immediately before writing, then filter out the matching peer(s).

Usage:
    python scripts/remove-bots-from-folder.py tanomivpnrobot
"""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from telethon import TelegramClient
from telethon.tl.functions.messages import UpdateDialogFilterRequest
from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder import db as finder_db
from tg_voice_transcriber.finder.folder import find_folder_by_id, resolve_folder


async def main() -> int:
    usernames = [a.lstrip("@").lower() for a in sys.argv[1:]]
    if not usernames:
        print("Usage: remove-bots-from-folder.py <bot> [bot ...]", file=sys.stderr)
        return 1

    cfg = get_config()
    client = TelegramClient(str(cfg.finder_session_path), cfg.api_id,
                            cfg.api_hash.get_secret_value())
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT AUTHORIZED"); return 1

    await finder_db.init_finder_db(cfg.finder_db_path)
    db_cfg = await finder_db.load_finder_config(cfg.finder_db_path)
    folder = await resolve_folder(client, title=cfg.finder_folder_title,
                                  folder_id=db_cfg.get("target_folder_id"))
    if folder is None:
        print("Folder not found"); return 1

    # Resolve target peer ids from usernames.
    target_ids: set[int] = set()
    for uname in usernames:
        try:
            ent = await client.get_entity(uname)
            target_ids.add(ent.id)
            print(f"  resolved @{uname} -> id={ent.id}")
        except Exception as exc:
            print(f"  @{uname}: resolve FAILED — {exc}")

    # Re-fetch live filter right before writing (clobber-proof).
    live = await find_folder_by_id(client, folder.id)
    if live is None:
        print("Folder disappeared"); return 1

    before = len(live.include_peers)
    kept = []
    removed = 0
    for p in live.include_peers:
        pid = getattr(p, "user_id", None) or getattr(p, "channel_id", None) or getattr(p, "chat_id", None)
        if pid in target_ids:
            removed += 1
            continue
        kept.append(p)

    live.include_peers = kept
    await client(UpdateDialogFilterRequest(id=live.id, filter=live))
    print(f"Removed {removed} peer(s). Folder: {before} -> {len(kept)} peers")

    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
