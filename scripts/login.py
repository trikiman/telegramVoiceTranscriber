#!/usr/bin/env python3
"""One-time interactive Telegram login.

Run this from your LOCAL machine (not the VPS) to create the session file.
The session file can then be securely copied to the VPS for production use.

Usage:
    python scripts/login.py

You will be prompted for:
    1. The login code Telegram sends to your app or via SMS
    2. Your 2FA password (if enabled)

On success, the session file is saved and subsequent runs of
``python -m tg_voice_transcriber`` will connect without prompting.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package is importable when running from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient  # noqa: E402

from tg_voice_transcriber.config import get_config  # noqa: E402


def main() -> None:
    """Run the interactive login flow."""
    print()
    print("━" * 50)
    print(" GSD ► One-time Telegram login")
    print("━" * 50)
    print()
    print("You'll be asked for the login code Telegram sends to your app/SMS.")
    print("If 2FA is enabled, you'll also be asked for your password.")
    print()
    print("⚠  Run this from your LOCAL machine, not the VPS.")
    print("   First-auth from a datacenter IP is a known ban trigger.")
    print()

    cfg = get_config()

    # Ensure session directory exists
    cfg.session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(
        session=str(cfg.session_path),
        api_id=cfg.api_id,
        api_hash=cfg.api_hash.get_secret_value(),
    )

    # client.start() is Telethon's high-level helper that handles:
    # - connect
    # - send code request
    # - prompt for code on stdin
    # - prompt for 2FA password if needed
    # - save session on success
    with client:
        client.start(phone=cfg.phone)
        me = client.loop.run_until_complete(client.get_me())
        username = me.username or me.first_name or str(me.id)

    session_abs = cfg.session_path.resolve()
    print()
    print(f"✓ Logged in as: @{username}")
    print(f"✓ Session saved to: {session_abs}")
    print()
    print("You can now run:  python -m tg_voice_transcriber")
    print(f"Or copy the session to your VPS:  scp {session_abs} ubuntu@your-vps:/var/lib/tg-voice-transcriber/userbot.session")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
    except Exception as exc:
        print(f"\n✗ Login failed: {exc}", file=sys.stderr)
        sys.exit(1)
