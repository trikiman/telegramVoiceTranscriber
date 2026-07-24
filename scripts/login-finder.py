#!/usr/bin/env python3
"""One-time interactive Telegram login for the VPN Trial Finder account.

This is a sibling of ``scripts/login.py``. The finder runs as a SEPARATE
Telegram account from the main voice/digest userbot — specifically the account
that owns the "30 дней впн" folder and the VPN channel subscriptions. It reuses
the same api_id/api_hash (those are tied to your developer identity, not the
account); only the phone number and session file differ.

Run this from your LOCAL machine (not the VPS) to create the finder session
file. First-auth from a datacenter IP is a known ban trigger. The session file
can then be securely copied to the VPS for production use.

Usage:
    # set TG_VOICE_FINDER_PHONE in your .env first (the finder account's phone)
    python scripts/login-finder.py

You will be prompted for:
    1. The login code Telegram sends to the finder account's app or via SMS
    2. That account's 2FA password (if enabled)

On success, the finder session file is saved and the finder subsystem will
connect without prompting.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package is importable when running from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient  # noqa: E402

from tg_voice_transcriber.config import get_config  # noqa: E402


def main() -> None:
    """Run the interactive login flow for the finder account."""
    print()
    print("━" * 50)
    print(" GSD ► One-time Telegram login (VPN Trial Finder)")
    print("━" * 50)
    print()
    print("This logs in the SEPARATE finder account (the one that owns the")
    print('"30 дней впн" folder), NOT your main voice/digest account.')
    print()
    print("You'll be asked for the login code Telegram sends to that account.")
    print("If 2FA is enabled, you'll also be asked for its password.")
    print()
    print("⚠  Run this from your LOCAL machine, not the VPS.")
    print("   First-auth from a datacenter IP is a known ban trigger.")
    print()

    cfg = get_config()

    if not cfg.finder_phone:
        print(
            "✗ TG_VOICE_FINDER_PHONE is not set.\n"
            "  Add the finder account's phone (E.164, e.g. +79161234567) to .env:\n"
            "      TG_VOICE_FINDER_PHONE=+79161234567",
            file=sys.stderr,
        )
        sys.exit(1)

    # Ensure session directory exists
    cfg.finder_session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(
        session=str(cfg.finder_session_path),
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
        client.start(phone=cfg.finder_phone)
        me = client.loop.run_until_complete(client.get_me())
        username = me.username or me.first_name or str(me.id)

    session_abs = cfg.finder_session_path.resolve()
    print()
    print(f"✓ Logged in as: @{username}")
    print(f"✓ Finder session saved to: {session_abs}")
    print()
    print("Verify this is the account that owns the '30 дней впн' folder.")
    print(
        "Or copy the session to your VPS:  "
        f"scp {session_abs} ubuntu@your-vps:/var/lib/tg-voice-transcriber/finder.session"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
    except Exception as exc:
        print(f"\n✗ Login failed: {exc}", file=sys.stderr)
        sys.exit(1)
