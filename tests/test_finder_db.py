"""Tests for finder.db — schema migration safety (verified_good column)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tg_voice_transcriber.finder import db as finder_db


@pytest.mark.asyncio
async def test_fresh_db_has_verified_good_column(tmp_path: Path) -> None:
    path = tmp_path / "finder.db"
    await finder_db.init_finder_db(path)

    con = sqlite3.connect(str(path))
    columns = {row[1] for row in con.execute("PRAGMA table_info(found_offers)")}
    con.close()
    assert "verified_good" in columns


@pytest.mark.asyncio
async def test_init_is_idempotent_across_repeated_calls(tmp_path: Path) -> None:
    """Simulates an already-live pre-v2 database getting the migration applied
    on the next startup, then started again (e.g. a service restart) — the
    guarded ALTER TABLE must not raise on the second, third, ... call."""
    path = tmp_path / "finder.db"
    await finder_db.init_finder_db(path)
    await finder_db.init_finder_db(path)
    await finder_db.init_finder_db(path)

    con = sqlite3.connect(str(path))
    columns = [row[1] for row in con.execute("PRAGMA table_info(found_offers)")]
    con.close()
    # Column exists exactly once — no duplicate-column corruption from
    # repeated ALTER TABLE attempts.
    assert columns.count("verified_good") == 1


@pytest.mark.asyncio
async def test_pre_v2_row_defaults_to_verified_good(tmp_path: Path) -> None:
    """A row inserted before schema v2 (no verified_good passed at the SQL
    layer) must default to verified_good=1 — only good offers were ever
    recorded prior to two-stage verification."""
    path = tmp_path / "finder.db"
    await finder_db.init_finder_db(path)

    con = sqlite3.connect(str(path))
    con.execute(
        "INSERT INTO found_offers (target_bot, offer_hash, source_channel_id, summary) "
        "VALUES (?, ?, ?, ?)",
        ("@oldbot", "abc123", 1, "legacy row"),
    )
    con.commit()
    verified_good = con.execute(
        "SELECT verified_good FROM found_offers WHERE target_bot = ?", ("@oldbot",)
    ).fetchone()[0]
    con.close()
    assert verified_good == 1


@pytest.mark.asyncio
async def test_record_found_offer_persists_verified_good_false(tmp_path: Path) -> None:
    path = tmp_path / "finder.db"
    await finder_db.init_finder_db(path)

    inserted = await finder_db.record_found_offer(
        path,
        target_bot="@badbot",
        offer_hash="hash1",
        source_channel_id=1,
        source_message_id=None,
        trial_days=None,
        trial_price_rub=None,
        summary="REJECTED after live check",
        verified_good=False,
    )
    assert inserted is True

    offers = await finder_db.list_found_offers(path)
    assert offers[0]["target_bot"] == "@badbot"
    assert offers[0]["verified_good"] == 0

    # Dedupe still applies regardless of verified_good — we never want to
    # re-/start a bot whose ad text we've already debunked.
    assert await finder_db.offer_already_found(path, "@badbot", "hash1")


@pytest.mark.asyncio
async def test_record_found_offer_defaults_verified_good_true(tmp_path: Path) -> None:
    """Callers that don't pass verified_good (e.g. the passive scheduler)
    keep today's behavior."""
    path = tmp_path / "finder.db"
    await finder_db.init_finder_db(path)

    await finder_db.record_found_offer(
        path,
        target_bot="@goodbot",
        offer_hash="hash2",
        source_channel_id=1,
        source_message_id=None,
        trial_days=30,
        trial_price_rub=0.0,
        summary="GoodVPN — 30 days free",
    )

    offers = await finder_db.list_found_offers(path)
    assert offers[0]["verified_good"] == 1
