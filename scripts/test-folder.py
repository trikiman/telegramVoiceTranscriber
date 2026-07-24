#!/usr/bin/env python3
"""Test script: verify folder.py can find and read the "30 дней впн" folder.

Usage:
    python scripts/test-folder.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.folder import find_folder_by_title


async def main() -> None:
    cfg = get_config()

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

    # Test: find the "30 дней впн" folder
    folder = await find_folder_by_title(client, "30 дней впн")

    if folder is None:
        print('✗ Folder "30 дней впн" not found')
        await client.disconnect()
        sys.exit(1)

    print(f'✓ Found folder "30 дней впн"')
    print(f"  ID: {folder.id}")
    print(f"  Peers: {len(folder.include_peers)} included")
    print(f"  Pinned: {len(folder.pinned_peers)}")

    # List first 5 peers for verification
    if folder.include_peers:
        print(f"  First few peers:")
        for i, peer in enumerate(folder.include_peers[:5], 1):
            peer_type = type(peer).__name__
            peer_id = getattr(peer, "user_id", None) or getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)
            print(f"    {i}. {peer_type} id={peer_id}")

    await client.disconnect()
    print("\n✓ Test passed")


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
