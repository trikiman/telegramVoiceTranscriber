"""Mute helper for the VPN Trial Finder.

Muting is MANDATORY for every channel/bot the finder touches — the user does
not want notification spam on the finder account. This is the single source of
truth for "mute forever" so the starter, the channel-subscriber, and the
proxy-harvester all behave identically.
"""

from __future__ import annotations

import structlog
from telethon import TelegramClient
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.types import InputNotifyPeer, InputPeerNotifySettings

log = structlog.get_logger()

# Max int32 — Telegram treats this as "muted forever".
MUTE_FOREVER = 2**31 - 1


async def mute_peer(client: TelegramClient, peer) -> bool:
    """Mute a peer (channel/bot/user) forever on the given client.

    Args:
        client: connected TelegramClient (the FINDER session, never the main one)
        peer: anything client.get_input_entity accepts — username, id, or entity

    Returns:
        True if muted, False on error (non-fatal — caller decides).
    """
    try:
        input_peer = await client.get_input_entity(peer)
        await client(
            UpdateNotifySettingsRequest(
                peer=InputNotifyPeer(peer=input_peer),
                settings=InputPeerNotifySettings(mute_until=MUTE_FOREVER),
            )
        )
        log.info("peer_muted", peer=str(peer))
        return True
    except Exception as exc:
        log.error("mute_failed", peer=str(peer), error=str(exc))
        return False
