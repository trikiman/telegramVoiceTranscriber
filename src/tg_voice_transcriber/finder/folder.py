"""Telegram folder (dialog filter) operations for the VPN Trial Finder.

Folders are Telegram's "dialog filters" — the tabs you see in the chat list.
This module resolves the "30 дней впн" folder by title and adds VPN bots to it.
"""

from __future__ import annotations

from typing import Any

import structlog
from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest
from telethon.tl.types import DialogFilter, InputPeerUser, InputPeerChannel, InputPeerChat

log = structlog.get_logger()


async def find_folder_by_title(client: TelegramClient, title: str) -> DialogFilter | None:
    """Find a folder by its title. Returns None if not found.

    Args:
        client: connected TelegramClient
        title: folder title to search for (case-sensitive)

    Returns:
        The DialogFilter if found, None otherwise.
    """
    try:
        result = await client(GetDialogFiltersRequest())
    except Exception as exc:
        log.error("folder_list_failed", error=str(exc))
        return None

    filters = getattr(result, "filters", result)

    for f in filters:
        if not isinstance(f, DialogFilter):
            continue

        folder_title = getattr(f, "title", None)
        # In newer Telegram layers, title may be a TextWithEntities object
        text = getattr(folder_title, "text", folder_title)

        if text == title:
            log.info("folder_found", title=title, folder_id=f.id, peers=len(f.include_peers))
            return f

    log.warning("folder_not_found", title=title, total_folders=len(filters))
    return None


async def add_peer_to_folder(
    client: TelegramClient,
    folder: DialogFilter,
    peer_id: int,
    access_hash: int | None = None,
) -> bool:
    """Add a peer (user/bot/channel) to a folder's include_peers list.

    Args:
        client: connected TelegramClient
        folder: the DialogFilter to update
        peer_id: Telegram peer id (for bots, the raw user_id; for channels, raw channel_id)
        access_hash: access hash if known (required for users/bots, optional for channels)

    Returns:
        True if successfully added, False on error or if already present.
    """
    # Build InputPeer — bots are InputPeerUser, channels are InputPeerChannel
    # The peer_id sign tells us: positive = user/bot, negative = channel/group
    if peer_id > 0:
        # User or bot
        if access_hash is None:
            log.error("add_peer_missing_access_hash", peer_id=peer_id)
            return False
        input_peer = InputPeerUser(user_id=peer_id, access_hash=access_hash)
    elif peer_id < 0:
        # Channel or supergroup — Telegram stores channel_id as -100<raw_id>
        # Strip the -100 prefix if present
        raw_id = abs(peer_id)
        if raw_id > 10**12:  # likely has -100 prefix
            raw_id = int(str(raw_id)[3:])

        # For channels we can try without access_hash (Telegram often resolves it)
        if access_hash:
            input_peer = InputPeerChannel(channel_id=raw_id, access_hash=access_hash)
        else:
            # Fallback: resolve via get_entity
            try:
                entity = await client.get_entity(peer_id)
                input_peer = await client.get_input_entity(entity)
            except Exception as exc:
                log.error("add_peer_resolve_failed", peer_id=peer_id, error=str(exc))
                return False
    else:
        log.error("add_peer_invalid_id", peer_id=peer_id)
        return False

    # Check if already present
    for existing in folder.include_peers:
        if _peer_matches(existing, input_peer):
            log.debug("peer_already_in_folder", peer_id=peer_id, folder_id=folder.id)
            return False

    # Clone the folder with the new peer appended
    new_include_peers = list(folder.include_peers) + [input_peer]

    updated_filter = DialogFilter(
        id=folder.id,
        title=folder.title,
        pinned_peers=folder.pinned_peers,
        include_peers=new_include_peers,
        exclude_peers=folder.exclude_peers,
        contacts=folder.contacts,
        non_contacts=folder.non_contacts,
        groups=folder.groups,
        broadcasts=folder.broadcasts,
        bots=folder.bots,
        exclude_muted=folder.exclude_muted,
        exclude_read=folder.exclude_read,
        exclude_archived=folder.exclude_archived,
        emoticon=getattr(folder, "emoticon", None),
    )

    try:
        await client(UpdateDialogFilterRequest(id=folder.id, filter=updated_filter))
        log.info("peer_added_to_folder", peer_id=peer_id, folder_id=folder.id, folder_title=folder.title)
        return True
    except Exception as exc:
        log.error("folder_update_failed", peer_id=peer_id, folder_id=folder.id, error=str(exc))
        return False


def _peer_matches(peer_a: Any, peer_b: Any) -> bool:
    """Check if two InputPeer objects refer to the same entity."""
    # Compare by type and id
    type_a = type(peer_a).__name__
    type_b = type(peer_b).__name__

    if type_a != type_b:
        return False

    if isinstance(peer_a, InputPeerUser):
        return peer_a.user_id == peer_b.user_id
    elif isinstance(peer_a, InputPeerChannel):
        return peer_a.channel_id == peer_b.channel_id
    elif isinstance(peer_a, InputPeerChat):
        return peer_a.chat_id == peer_b.chat_id

    return False
