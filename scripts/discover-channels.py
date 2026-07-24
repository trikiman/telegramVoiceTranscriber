#!/usr/bin/env python3
"""Discover NEW VPN channels/bots to expand the finder's scan list.

Two discovery sources, both READ-ONLY (no join, no /start, no folder changes):

  1) Global Telegram search (contacts.SearchRequest) over VPN keywords —
     surfaces public VPN channels you are NOT yet subscribed to.
  2) Sponsored-ad sweep across your subscribed channels — the ads point to
     VPN channels/bots you may not know about yet.

Prints candidates you don't already follow, deduped, VPN-scored. Review the
list, then subscribe to the good ones manually (or we add an auto-join later).

Usage:
    python scripts/discover-channels.py [search_limit_per_term]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.messages import GetSponsoredMessagesRequest
from telethon.tl.types import Channel, SponsoredMessage, User

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.links import parse_tme_target

# VPN-relevant search terms (Cyrillic + Latin). Global search matches titles,
# usernames, and bios of public channels/users.
SEARCH_TERMS = [
    "vpn", "впн", "бесплатный vpn", "free vpn", "vpn бесплатно",
    "обход блокировок", "vpn пробный", "vpn триал", "vless", "прокси vpn",
]

# Title/username markers that make a candidate worth scanning.
_VPN_MARKERS = (
    "vpn", "впн", "proxy", "прокси", "vless", "mtproto", "обход", "unblock",
)


def _is_vpnish(title: str, username: str | None) -> bool:
    hay = f"{title} {username or ''}".lower()
    return any(m in hay for m in _VPN_MARKERS)


async def main() -> None:
    search_limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20

    cfg = get_config()
    client = TelegramClient(
        str(cfg.finder_session_path),
        cfg.api_id,
        cfg.api_hash.get_secret_value(),
    )
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT AUTHORIZED — run scripts/login-finder.py first", file=sys.stderr)
        sys.exit(1)

    me = await client.get_me()
    print(f"Connected as: {me.first_name} {me.last_name or ''}\n")

    # Build the set of channels/bots we ALREADY follow (by username, lowercased).
    already: set[str] = set()
    subscribed_channel_ids: list[int] = []
    async for dialog in client.iter_dialogs():
        ent = dialog.entity
        uname = getattr(ent, "username", None)
        if uname:
            already.add(uname.lower())
        if dialog.is_channel and not dialog.is_group:
            subscribed_channel_ids.append(dialog.id)

    print(f"Already following {len(already)} entities; "
          f"{len(subscribed_channel_ids)} channels subscribed.\n")

    # candidate username -> (title, source, extra)
    candidates: dict[str, tuple[str, str, str]] = {}

    # --- Source 1: global search ------------------------------------------
    print("=== Global search ===")
    for term in SEARCH_TERMS:
        try:
            res = await client(SearchRequest(q=term, limit=search_limit))
        except Exception as exc:
            print(f"  search '{term}' failed: {exc}")
            await asyncio.sleep(2)
            continue

        found_here = 0
        for chat in list(res.chats) + list(res.users):
            uname = getattr(chat, "username", None)
            if not uname:
                continue
            ul = uname.lower()
            if ul in already or ul in candidates:
                continue
            title = getattr(chat, "title", None) or getattr(chat, "first_name", "") or uname
            if not _is_vpnish(title, uname):
                continue
            kind = "bot" if isinstance(chat, User) else "channel"
            candidates[ul] = (title, f"search:{term}", kind)
            found_here += 1
        print(f"  '{term}': +{found_here} new VPN-ish")
        await asyncio.sleep(1.5)  # be gentle on search flood limits

    # --- Source 2: sponsored-ad sweep -------------------------------------
    print("\n=== Sponsored-ad sweep ===")
    swept = 0
    for cid in subscribed_channel_ids:
        try:
            peer = await client.get_input_entity(cid)
            result = await client(GetSponsoredMessagesRequest(peer=peer))
        except Exception:
            continue
        for msg in getattr(result, "messages", []):
            if not isinstance(msg, SponsoredMessage):
                continue
            lt = parse_tme_target(
                getattr(msg, "url", None),
                button_text=getattr(msg, "button_text", None),
            )
            if lt is None:
                continue
            ul = lt.username.lower()
            if ul in already or ul in candidates:
                continue
            title = getattr(msg, "title", None) or lt.username
            if not _is_vpnish(title, lt.username):
                continue
            kind = "bot" if lt.is_bot else "channel"
            candidates[ul] = (title, "sponsored-ad", kind)
        swept += 1
        await asyncio.sleep(2)  # sponsored API is rate-limited
    print(f"  swept {swept} subscribed channels for ads")

    # --- Report -----------------------------------------------------------
    print(f"\n=== {len(candidates)} NEW VPN candidates (not yet followed) ===")
    bots = [(u, t, s) for u, (t, s2, k) in candidates.items() for s in [s2] if candidates[u][2] == "bot"]
    chans = [(u, candidates[u][0], candidates[u][1]) for u in candidates if candidates[u][2] == "channel"]

    print(f"\n  CHANNELS ({len(chans)}) — subscribe + scan their feeds:")
    for u, title, src in sorted(chans):
        print(f"    @{u:<24} {title[:40]:<40} [{src}]")

    print(f"\n  BOTS ({len(bots)}) — probe directly for trial terms:")
    for u, title, src in sorted(bots):
        print(f"    @{u:<24} {title[:40]:<40} [{src}]")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
