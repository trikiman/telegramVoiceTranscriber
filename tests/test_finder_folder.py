"""Tests for finder.folder — tolerant matching, id lookup, clobber-proof adds."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from telethon.tl.types import DialogFilter, InputPeerUser

from tg_voice_transcriber.finder.folder import (
    add_peer_to_folder,
    find_folder_by_id,
    find_folder_by_title,
    resolve_folder,
)


def _folder(fid: int, title: str, include=None) -> DialogFilter:
    """Build a minimal-but-real DialogFilter for tests."""
    return DialogFilter(
        id=fid,
        title=title,
        pinned_peers=[],
        include_peers=list(include or []),
        exclude_peers=[],
    )


class _FakeClient:
    """Fake Telethon client that persists DialogFilter updates in memory."""

    def __init__(self, filters: list[DialogFilter]) -> None:
        self._filters = filters
        self.update_calls: list = []

    async def __call__(self, request):
        name = type(request).__name__
        if name == "GetDialogFiltersRequest":
            return SimpleNamespace(filters=list(self._filters))
        if name == "UpdateDialogFilterRequest":
            self.update_calls.append(request)
            for i, f in enumerate(self._filters):
                if f.id == request.id:
                    self._filters[i] = request.filter
                    break
            return True
        raise AssertionError(f"unexpected request {name}")


@pytest.mark.asyncio
async def test_find_by_title_is_case_and_space_insensitive() -> None:
    client = _FakeClient([_folder(7, "10+ days vpn")])
    for variant in ("10+ days vpn", "10+ DAYS VPN", "  10+   days  vpn "):
        found = await find_folder_by_title(client, variant)
        assert found is not None, variant
        assert found.id == 7


@pytest.mark.asyncio
async def test_find_by_title_does_not_conflate_distinct_names() -> None:
    client = _FakeClient([_folder(7, "10+ days vpn")])
    # "10 days vpn" (no plus) is a genuinely different name — must NOT match.
    assert await find_folder_by_title(client, "10 days vpn") is None


@pytest.mark.asyncio
async def test_find_by_id() -> None:
    client = _FakeClient([_folder(1, "a"), _folder(2, "b")])
    found = await find_folder_by_id(client, 2)
    assert found is not None and found.id == 2
    assert await find_folder_by_id(client, 999) is None


@pytest.mark.asyncio
async def test_resolve_prefers_id_then_title() -> None:
    client = _FakeClient([_folder(5, "old name")])
    # id known and valid → returned regardless of title mismatch
    found = await resolve_folder(client, title="whatever", folder_id=5)
    assert found is not None and found.id == 5
    # stale id → fall back to title
    found2 = await resolve_folder(client, title="old name", folder_id=123)
    assert found2 is not None and found2.id == 5


@pytest.mark.asyncio
async def test_add_multiple_peers_does_not_clobber() -> None:
    """The core bug: adding a 2nd bot must not erase the 1st."""
    folder = _folder(3, "10+ days vpn")
    client = _FakeClient([folder])

    ok_a = await add_peer_to_folder(client, folder, peer_id=100, access_hash=11)
    ok_b = await add_peer_to_folder(client, folder, peer_id=200, access_hash=22)
    assert ok_a and ok_b

    live = await find_folder_by_id(client, 3)
    user_ids = sorted(
        p.user_id for p in live.include_peers if isinstance(p, InputPeerUser)
    )
    assert user_ids == [100, 200], "both bots must be present — no clobber"


@pytest.mark.asyncio
async def test_add_same_peer_twice_is_idempotent() -> None:
    folder = _folder(4, "vpn")
    client = _FakeClient([folder])

    first = await add_peer_to_folder(client, folder, peer_id=100, access_hash=11)
    second = await add_peer_to_folder(client, folder, peer_id=100, access_hash=11)
    assert first is True
    assert second is False  # already present

    live = await find_folder_by_id(client, 4)
    assert len([p for p in live.include_peers if isinstance(p, InputPeerUser)]) == 1


@pytest.mark.asyncio
async def test_add_user_without_access_hash_fails_cleanly() -> None:
    folder = _folder(6, "vpn")
    client = _FakeClient([folder])
    ok = await add_peer_to_folder(client, folder, peer_id=100, access_hash=None)
    assert ok is False
