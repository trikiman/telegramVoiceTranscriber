#!/usr/bin/env python3
"""Point the finder/harvester at your VPN folder — and pin it by id.

Resolves the folder by title on the FINDER account, writes the title + numeric
id into finder.db, so both the always-on finder and the harvester file bots into
the right folder even after future renames.

Run this once after renaming your folder (e.g. to "10+ days vpn").

Usage:
    python scripts/set-finder-folder.py                 # uses TG_VOICE_FINDER_FOLDER_TITLE
    python scripts/set-finder-folder.py --title "10+ days vpn"
    python scripts/set-finder-folder.py --list          # just list your folders
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder import db as finder_db
from tg_voice_transcriber.finder.folder import _title_text, list_dialog_filters, resolve_folder


async def main() -> int:
    ap = argparse.ArgumentParser(description="Set/pin the finder target folder")
    ap.add_argument("--title", default=None, help="Folder title (default: TG_VOICE_FINDER_FOLDER_TITLE)")
    ap.add_argument("--list", action="store_true", help="List folders and exit")
    args = ap.parse_args()

    cfg = get_config()
    title = args.title or cfg.finder_folder_title

    client = TelegramClient(
        str(cfg.finder_session_path),
        cfg.api_id,
        cfg.api_hash.get_secret_value(),
    )
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT AUTHORIZED — run scripts/login-finder.py first", file=sys.stderr)
        await client.disconnect()
        return 1

    folders = await list_dialog_filters(client)
    print(f"Your editable folders ({len(folders)}):")
    for f in folders:
        print(f"  id={f.id:<4} {_title_text(f)!r}  ({len(f.include_peers)} peers)")

    if args.list:
        await client.disconnect()
        return 0

    folder = await resolve_folder(client, title=title)
    if folder is None:
        print(f"\n✗ No editable folder matches title {title!r}. "
              f"Fix the name above or pass --title.", file=sys.stderr)
        await client.disconnect()
        return 1

    await finder_db.init_finder_db(cfg.finder_db_path)
    await finder_db.set_target_folder(cfg.finder_db_path, title=title, folder_id=folder.id)
    print(f"\n✓ Pinned folder {title!r} (id={folder.id}) in {cfg.finder_db_path}")
    print("  The finder and harvester will now file bots here, rename-proof.")

    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
