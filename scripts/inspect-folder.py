#!/usr/bin/env python3
"""Dump the target folder's flags + peer list to explain UI visibility."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from telethon import TelegramClient

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.folder import _title_text, list_dialog_filters


async def main() -> int:
    cfg = get_config()
    client = TelegramClient(str(cfg.finder_session_path), cfg.api_id,
                            cfg.api_hash.get_secret_value())
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT AUTHORIZED")
        return 1

    for f in await list_dialog_filters(client):
        print(f"folder id={f.id} title={_title_text(f)!r}")
        for flag in ("contacts", "non_contacts", "groups", "broadcasts", "bots",
                     "exclude_muted", "exclude_read", "exclude_archived"):
            print(f"    {flag:16} = {getattr(f, flag, None)}")
        print(f"    include_peers    = {len(f.include_peers)}")
        print(f"    pinned_peers     = {len(f.pinned_peers)}")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
