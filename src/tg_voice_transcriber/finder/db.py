"""SQLite schema and helpers for VPN Trial Finder state.

Tables:
    finder_config   — single-row config (target folder id, enabled flag, etc.)
    found_offers    — dedupe cache of offers we've already collected
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finder_config (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    enabled           INTEGER NOT NULL DEFAULT 1,
    target_folder_id  INTEGER,
    target_folder_title TEXT NOT NULL DEFAULT '10 дней vpn',
    scan_interval_s   INTEGER NOT NULL DEFAULT 3600,
    updated_at        REAL NOT NULL DEFAULT (strftime('%s','now'))
);

-- found_offers: dedupe cache of offers we've already collected.
-- Keyed by (target_bot, offer_hash) so we don't file the same bot twice
-- for the same offer, but DO file it again if a new/different offer appears.
CREATE TABLE IF NOT EXISTS found_offers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_bot      TEXT NOT NULL,
    offer_hash      TEXT NOT NULL,
    source_channel_id INTEGER NOT NULL,
    source_message_id INTEGER,
    trial_days      INTEGER,
    trial_price_rub REAL,
    summary         TEXT NOT NULL,
    found_at        REAL NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(target_bot, offer_hash)
);

CREATE INDEX IF NOT EXISTS idx_found_offers_bot
    ON found_offers(target_bot);

CREATE INDEX IF NOT EXISTS idx_found_offers_found_at
    ON found_offers(found_at);
"""


def compute_offer_hash(text: str) -> str:
    """Short hash of the first 300 chars of an offer for dedup."""
    normalized = (text or "").strip()[:300].lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


async def init_finder_db(path: Path) -> None:
    """Create the finder database file and all tables if they don't exist."""
    import aiosqlite

    path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(path)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(SCHEMA_SQL)

        # Ensure config row exists
        await db.execute(
            "INSERT OR IGNORE INTO finder_config (id, target_folder_title) VALUES (1, '10 дней vpn')"
        )

        # Record schema version
        await db.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )

        await db.commit()

    log.info("finder_db_ready", path=str(path), schema_version=SCHEMA_VERSION)


async def load_finder_config(path: Path) -> dict[str, Any]:
    """Load the single-row finder config."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        cur = await db.execute(
            "SELECT enabled, target_folder_id, target_folder_title, scan_interval_s "
            "FROM finder_config WHERE id = 1"
        )
        row = await cur.fetchone()
        if row is None:
            return {
                "enabled": True,
                "target_folder_id": None,
                "target_folder_title": "10 дней vpn",
                "scan_interval_s": 3600,
            }

        return {
            "enabled": bool(row[0]),
            "target_folder_id": row[1],
            "target_folder_title": row[2] or "10 дней vpn",
            "scan_interval_s": int(row[3]),
        }


async def save_finder_config(path: Path, config: dict[str, Any]) -> None:
    """Upsert the finder config row."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        await db.execute(
            """
            UPDATE finder_config
               SET enabled = ?,
                   target_folder_id = ?,
                   target_folder_title = ?,
                   scan_interval_s = ?,
                   updated_at = strftime('%s','now')
             WHERE id = 1
            """,
            (
                1 if config.get("enabled", True) else 0,
                config.get("target_folder_id"),
                config.get("target_folder_title", "10 дней vpn"),
                config.get("scan_interval_s", 3600),
            ),
        )
        await db.commit()


async def offer_already_found(path: Path, target_bot: str, offer_hash: str) -> bool:
    """Check if we've already collected this offer for this bot."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        cur = await db.execute(
            "SELECT 1 FROM found_offers WHERE target_bot = ? AND offer_hash = ? LIMIT 1",
            (target_bot, offer_hash),
        )
        return (await cur.fetchone()) is not None


async def record_found_offer(
    path: Path,
    target_bot: str,
    offer_hash: str,
    source_channel_id: int,
    source_message_id: int | None,
    trial_days: int | None,
    trial_price_rub: float | None,
    summary: str,
) -> bool:
    """Record a newly found offer. Returns True if newly inserted, False if duplicate."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        try:
            await db.execute(
                """
                INSERT INTO found_offers (target_bot, offer_hash, source_channel_id, source_message_id,
                                          trial_days, trial_price_rub, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (target_bot, offer_hash, source_channel_id, source_message_id, trial_days, trial_price_rub, summary),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def list_found_offers(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Return recently found offers for debugging."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT target_bot, summary, trial_days, trial_price_rub, found_at
              FROM found_offers
             ORDER BY found_at DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
