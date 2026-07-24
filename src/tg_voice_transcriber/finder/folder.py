"""Telegram folder (dialog filter) operations for the VPN Trial Finder.

Folders are Telegram's "dialog filters" — the tabs you see in the chat list.
This module resolves the target VPN folder and adds VPN bots to it.

Two robustness properties matter here and were the source of real bugs:

1. **Rename-proof resolution.** Titles are matched case-insensitively and
   whitespace-normalised, and once resolved the caller should track the folder
   by its numeric ``id`` (see :func:`resolve_folder`). A folder renamed from
   "10 дней vpn" to "10+ days vpn" must not silently break the pipeline.

2. **Clobber-proof writes.** :func:`add_peer_to_folder` re-fetches the *live*
   filter immediately before each write and mutates it in place, so adding
   several bots in one run never drops earlier additions (the previous
   implementation rebuilt from a stale in-memory ``include_peers`` and erased
   any peer added earlier in the same cycle).
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from telethon import TelegramClient
from telethon.tl.functions.messages import (
    GetDialogFiltersRequest,
    UpdateDialogFilterRequest,
)
from telethon.tl.types import (
    DialogFilter,
    InputPeerChannel,
    InputPeerChat,
    InputPeerUser,
)

log = structlog.get_logger()


def _title_text(folder: Any) -> str | None:
    """Extract the plain title string from a DialogFilter.

    In newer Telegram layers the title is a ``TextWithEntities`` object with a
    ``.text`` attribute; in older ones it is a bare string.
    """
    raw = getattr(folder, "title", None)
    text = getattr(raw, "text", raw)
    return text if isinstance(text, str) else None


def _normalize(title: str | None) -> str:
    """Normalise a folder title for tolerant comparison.

    Case-folded and whitespace-collapsed so that "10+ days VPN",
    "10+  days  vpn" and "10+ days vpn" all compare equal. We deliberately do
    NOT strip punctuation — "10+ days vpn" and "10 days vpn" are genuinely
    different names and should not be conflated.
    """
    if not title:
        return ""
    return re.sub(r"\s+", " ", title).strip().casefold()


async def list_dialog_filters(client: TelegramClient) -> list[DialogFilter]:
    """Return all editable folder filters (excludes default/chatlist entries)."""
    try:
        result = await client(GetDialogFiltersRequest())
    except Exception as exc:
        log.error("folder_list_failed", error=str(exc))
        return []
    filters = getattr(result, "filters", result)
    return [f for f in filters if isinstance(f, DialogFilter)]


async def find_folder_by_title(client: TelegramClient, title: str) -> DialogFilter | None:
    """Find a folder by title (case-insensitive, whitespace-tolerant).

    Returns the :class:`DialogFilter` if found, else None. If a *shared*
    (chatlist) folder matches the title, we log a clear hint — those cannot be
    edited via ``UpdateDialogFilterRequest`` the same way.
    """
    want = _normalize(title)

    try:
        result = await client(GetDialogFiltersRequest())
    except Exception as exc:
        log.error("folder_list_failed", error=str(exc))
        return None
    filters = getattr(result, "filters", result)

    editable: list[DialogFilter] = []
    for f in filters:
        if isinstance(f, DialogFilter):
            editable.append(f)
            continue
        # Detect a same-named shared/chatlist folder and warn — common footgun.
        other_title = _title_text(f)
        if other_title is not None and _normalize(other_title) == want:
            log.warning(
                "folder_matched_but_not_editable",
                title=title,
                kind=type(f).__name__,
                hint="Shared/chatlist folders can't be edited; use a normal folder.",
            )

    for f in editable:
        if _normalize(_title_text(f)) == want:
            log.info(
                "folder_found",
                title=title,
                folder_id=f.id,
                peers=len(f.include_peers),
            )
            return f

    log.warning(
        "folder_not_found",
        title=title,
        available=[_title_text(f) for f in editable],
    )
    return None


async def find_folder_by_id(client: TelegramClient, folder_id: int) -> DialogFilter | None:
    """Find an editable folder by its numeric id. Returns None if missing."""
    for f in await list_dialog_filters(client):
        if f.id == folder_id:
            return f
    log.warning("folder_id_not_found", folder_id=folder_id)
    return None


async def resolve_folder(
    client: TelegramClient,
    *,
    title: str,
    folder_id: int | None = None,
) -> DialogFilter | None:
    """Resolve the target folder, preferring a known id over the title.

    Resolution order:
      1. If ``folder_id`` is known, look it up by id (rename-proof).
      2. Otherwise (or if the id no longer exists), fall back to the title.

    Callers should persist ``folder.id`` after the first successful resolve so
    subsequent runs are immune to folder renames.
    """
    if folder_id is not None:
        found = await find_folder_by_id(client, folder_id)
        if found is not None:
            return found
        log.info("folder_id_stale_falling_back_to_title", folder_id=folder_id, title=title)
    return await find_folder_by_title(client, title)


def _build_input_peer(peer_id: int, access_hash: int | None):
    """Build an InputPeer from a signed peer id (positive=user/bot)."""
    if peer_id > 0:
        if access_hash is None:
            return None
        return InputPeerUser(user_id=peer_id, access_hash=access_hash)
    # Channel/supergroup — strip the -100 prefix Telegram uses in signed ids.
    raw_id = abs(peer_id)
    if raw_id > 10**12:
        raw_id = int(str(raw_id)[3:])
    if access_hash is not None:
        return InputPeerChannel(channel_id=raw_id, access_hash=access_hash)
    return None  # caller resolves via get_input_entity


async def add_peer_to_folder(
    client: TelegramClient,
    folder: DialogFilter,
    peer_id: int,
    access_hash: int | None = None,
) -> bool:
    """Add a peer (bot/channel) to a folder's include_peers — clobber-proof.

    The live filter is re-fetched by ``folder.id`` immediately before writing,
    so multiple additions in one run accumulate correctly instead of each write
    overwriting the last.

    Args:
        client: connected TelegramClient
        folder: the target folder (only its ``id`` is authoritative here)
        peer_id: Telegram peer id (positive = user/bot, negative = channel)
        access_hash: access hash (required for users/bots)

    Returns:
        True if newly added; False on error or if already present.
    """
    if peer_id == 0:
        log.error("add_peer_invalid_id", peer_id=peer_id)
        return False

    # Build the InputPeer. For channels without an access hash, resolve it.
    input_peer = _build_input_peer(peer_id, access_hash)
    if input_peer is None:
        if peer_id > 0:
            log.error("add_peer_missing_access_hash", peer_id=peer_id)
            return False
        try:
            entity = await client.get_entity(peer_id)
            input_peer = await client.get_input_entity(entity)
        except Exception as exc:
            log.error("add_peer_resolve_failed", peer_id=peer_id, error=str(exc))
            return False

    # Re-fetch the LIVE filter so we never write from stale state.
    live = await find_folder_by_id(client, folder.id)
    if live is None:
        log.error("add_peer_folder_gone", folder_id=folder.id)
        return False

    for existing in live.include_peers:
        if _peer_matches(existing, input_peer):
            log.debug("peer_already_in_folder", peer_id=peer_id, folder_id=live.id)
            return False

    # Mutate the live object in place — preserves every field (color, emoticon,
    # flags) across Telegram layers, unlike a manual field-by-field rebuild.
    live.include_peers = list(live.include_peers) + [input_peer]

    try:
        await client(UpdateDialogFilterRequest(id=live.id, filter=live))
        log.info(
            "peer_added_to_folder",
            peer_id=peer_id,
            folder_id=live.id,
            total_peers=len(live.include_peers),
        )
        return True
    except Exception as exc:
        log.error("folder_update_failed", peer_id=peer_id, folder_id=live.id, error=str(exc))
        return False


def _peer_matches(peer_a: Any, peer_b: Any) -> bool:
    """Check if two InputPeer objects refer to the same entity."""
    if type(peer_a).__name__ != type(peer_b).__name__:
        return False
    if isinstance(peer_a, InputPeerUser):
        return peer_a.user_id == peer_b.user_id
    if isinstance(peer_a, InputPeerChannel):
        return peer_a.channel_id == peer_b.channel_id
    if isinstance(peer_a, InputPeerChat):
        return peer_a.chat_id == peer_b.chat_id
    return False
