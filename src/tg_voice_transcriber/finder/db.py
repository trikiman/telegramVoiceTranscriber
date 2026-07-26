"""SQLite schema and helpers for VPN Trial Finder state.

Tables:
    finder_config   — single-row config (target folder id, enabled flag, etc.)
    found_offers    — dedupe cache of offers we've already collected
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finder_config (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    enabled           INTEGER NOT NULL DEFAULT 1,
    target_folder_id  INTEGER,
    target_folder_title TEXT NOT NULL DEFAULT '10+ days vpn',
    scan_interval_s   INTEGER NOT NULL DEFAULT 3600,
    updated_at        REAL NOT NULL DEFAULT (strftime('%s','now'))
);

-- found_offers: dedupe cache of offers we've already collected.
-- Keyed by (target_bot, offer_hash) so we don't file the same bot twice
-- for the same offer, but DO file it again if a new/different offer appears.
-- verified_good: whether the offer survived LIVE welcome-screen verification
-- (finder/verify.py) — 0 means the ad/search-text looked good but the bot's
-- real /start screen debunked it (e.g. "free" was conditional on payment or
-- a review screenshot). Rows written before schema v2 default to 1 since
-- only good offers were ever recorded prior to two-stage verification.
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
    verified_good   INTEGER NOT NULL DEFAULT 1,
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
            "INSERT OR IGNORE INTO finder_config (id, target_folder_title) VALUES (1, '10+ days vpn')"
        )

        # Migration: pre-v2 databases have found_offers without verified_good.
        # Additive, safe, and idempotent — guarded by a column-existence check
        # rather than relying on try/except, since a bare ALTER TABLE would
        # otherwise fail loudly on every subsequent startup once the column
        # exists.
        cur = await db.execute("PRAGMA table_info(found_offers)")
        columns = {row[1] for row in await cur.fetchall()}
        if "verified_good" not in columns:
            try:
                await db.execute(
                    "ALTER TABLE found_offers "
                    "ADD COLUMN verified_good INTEGER NOT NULL DEFAULT 1"
                )
            except Exception as exc:  # noqa: BLE001 — defensive against a
                # concurrent migration race (two processes touching the same
                # file at once); "duplicate column" is the only expected
                # failure here and is harmless either way.
                log.debug("verified_good_migration_race", error=str(exc))

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
                "target_folder_title": "10+ days vpn",
                "scan_interval_s": 3600,
            }

        return {
            "enabled": bool(row[0]),
            "target_folder_id": row[1],
            "target_folder_title": row[2] or "10+ days vpn",
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
                config.get("target_folder_title", "10+ days vpn"),
                config.get("scan_interval_s", 3600),
            ),
        )
        await db.commit()


async def set_target_folder(
    path: Path,
    *,
    title: str,
    folder_id: int | None = None,
) -> None:
    """Update just the target folder title and (optionally) its resolved id.

    Ensures the config row exists first. Used by ``scripts/set-finder-folder.py``
    and by the scheduler's auto-heal path when it resolves a folder by title and
    wants to pin the id so future renames don't break resolution.
    """
    import aiosqlite

    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path)) as db:
        await db.execute(
            "INSERT OR IGNORE INTO finder_config (id, target_folder_title) VALUES (1, ?)",
            (title,),
        )
        await db.execute(
            """
            UPDATE finder_config
               SET target_folder_title = ?,
                   target_folder_id = ?,
                   updated_at = strftime('%s','now')
             WHERE id = 1
            """,
            (title, folder_id),
        )
        await db.commit()
    log.info("finder_target_folder_set", title=title, folder_id=folder_id)


async def offer_already_found(path: Path, target_bot: str, offer_hash: str) -> bool:
    """Check if we've already collected this offer for this bot."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        cur = await db.execute(
            "SELECT 1 FROM found_offers WHERE target_bot = ? AND offer_hash = ? LIMIT 1",
            (target_bot, offer_hash),
        )
        return (await cur.fetchone()) is not None


async def bot_examined_within(path: Path, target_bot: str, days: int) -> bool:
    """True if this BOT was live-examined within the last ``days``.

    Distinct from :func:`offer_already_found`, which keys on (bot, offer_hash)
    and therefore treats the same bot re-advertised with reworded copy as a
    brand-new candidate — burning a `/start` from the ban-safety budget to
    re-learn what we already know. A bot's actual terms rarely change, so once
    we've opened it we skip it for a while regardless of ad wording, leaving
    the budget for genuinely unseen bots.
    """
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        cur = await db.execute(
            "SELECT 1 FROM found_offers "
            " WHERE target_bot = ? AND found_at > strftime('%s','now') - ? "
            " LIMIT 1",
            (target_bot, days * 86400),
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
    verified_good: bool = True,
) -> bool:
    """Record a found offer. Returns True if newly inserted, False if duplicate.

    ``verified_good`` distinguishes a genuinely filed offer (survived live
    welcome-screen verification) from one recorded ONLY to stop the harvester
    re-`/start`-ing the same ad text on a future run after it was debunked
    (see finder/verify.py) — set False for the latter. Defaults True for
    callers (e.g. the passive v1.2 scheduler) that don't yet do two-stage
    verification.
    """
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        try:
            await db.execute(
                """
                INSERT INTO found_offers (target_bot, offer_hash, source_channel_id, source_message_id,
                                          trial_days, trial_price_rub, summary, verified_good)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_bot, offer_hash, source_channel_id, source_message_id,
                    trial_days, trial_price_rub, summary, 1 if verified_good else 0,
                ),
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
            SELECT target_bot, summary, trial_days, trial_price_rub, found_at, verified_good
              FROM found_offers
             ORDER BY found_at DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
