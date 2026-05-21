"""SQLite schema and async helpers for digest state.

Tables:
    config         — single-row user config (delivery chat, prefs, threshold, etc.)
    tracked_channels — channels the user wants digested
    post_buffer    — new posts waiting for the next scoring cycle
    dedupe_cache   — 24h cache of post hashes to skip near-duplicates
    stats          — per-cycle stats (for /digest stats)
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

SCHEMA_VERSION = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    delivery_chat_id  INTEGER,
    user_prefs_text   TEXT NOT NULL DEFAULT '',
    threshold         INTEGER NOT NULL DEFAULT 7,
    frequency_s       INTEGER NOT NULL DEFAULT 1800,
    paused            INTEGER NOT NULL DEFAULT 0,
    top_n             INTEGER NOT NULL DEFAULT 0,
    top_n_floor       INTEGER NOT NULL DEFAULT 4,
    updated_at        REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS tracked_channels (
    channel_id       INTEGER PRIMARY KEY,
    channel_title    TEXT NOT NULL,
    channel_username TEXT,
    added_at         REAL NOT NULL DEFAULT (strftime('%s','now'))
);

-- Blocklist: channels the user explicitly unsubscribed from.
-- Prevents default_track_all from re-adding them on the next post.
CREATE TABLE IF NOT EXISTS blocked_channels (
    channel_id       INTEGER PRIMARY KEY,
    channel_title    TEXT NOT NULL DEFAULT '',
    channel_username TEXT,
    blocked_at       REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS post_buffer (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id     INTEGER NOT NULL,
    message_id     INTEGER NOT NULL,
    received_at    REAL NOT NULL,
    text           TEXT NOT NULL,
    UNIQUE(channel_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_post_buffer_received
    ON post_buffer(received_at);

CREATE TABLE IF NOT EXISTS dedupe_cache (
    post_hash      TEXT PRIMARY KEY,
    first_seen_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dedupe_first_seen
    ON dedupe_cache(first_seen_at);

CREATE TABLE IF NOT EXISTS stats (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_start      REAL NOT NULL,
    cycle_end        REAL NOT NULL,
    msgs_scanned     INTEGER NOT NULL DEFAULT 0,
    msgs_delivered   INTEGER NOT NULL DEFAULT 0,
    llm_error        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_stats_cycle_end
    ON stats(cycle_end);
"""


@dataclass
class DigestConfig:
    """In-memory view of the single-row config table."""

    delivery_chat_id: int | None
    user_prefs_text: str
    threshold: int
    frequency_s: int
    paused: bool
    top_n: int = 0  # 0 = threshold mode, >0 = always show top N regardless of threshold
    top_n_floor: int = 4  # in top-N mode, drop posts scoring below this floor

    @property
    def ready(self) -> bool:
        """True when all required fields are set and we have at least one tracked channel."""
        return self.delivery_chat_id is not None


def compute_post_hash(text: str) -> str:
    """Short hash of the first 200 chars of a post for dedup."""
    normalized = (text or "").strip()[:200].lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


async def init_db(path: Path) -> None:
    """Create the digest database file and all tables if they don't exist.

    Sets WAL mode for better concurrency between the ingest handler and the
    scheduler task.

    Idempotent migrations are applied here for in-place upgrades of an existing
    database (CREATE TABLE IF NOT EXISTS only adds missing tables, not new
    columns on existing ones).
    """
    import aiosqlite

    path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(path)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(SCHEMA_SQL)

        # In-place migrations for older DBs (CREATE TABLE IF NOT EXISTS does
        # not add new columns to existing tables).
        await _migrate_add_column_if_missing(
            db, "config", "top_n_floor", "INTEGER NOT NULL DEFAULT 4"
        )

        # Ensure config row exists
        await db.execute(
            "INSERT OR IGNORE INTO config (id, user_prefs_text) VALUES (1, '')"
        )

        # Record schema version
        await db.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )

        await db.commit()

    log.info("digest_db_ready", path=str(path), schema_version=SCHEMA_VERSION)


async def _migrate_add_column_if_missing(
    db, table: str, column: str, type_clause: str
) -> None:
    """ALTER TABLE … ADD COLUMN if the column is not already present.

    SQLite's ALTER TABLE has no IF NOT EXISTS clause, so we read the table
    metadata via PRAGMA and only add when missing. Idempotent across restarts.
    """
    cur = await db.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    have = {r[1] for r in rows}  # row[1] is column name
    if column in have:
        return
    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_clause}")
    log.info("digest_db_migration_applied", table=table, column=column)


async def load_config(path: Path) -> DigestConfig:
    """Load the single-row config."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        cur = await db.execute(
            "SELECT delivery_chat_id, user_prefs_text, threshold, frequency_s, paused, "
            "COALESCE(top_n, 0), COALESCE(top_n_floor, 4) "
            "FROM config WHERE id = 1"
        )
        row = await cur.fetchone()
        if row is None:
            return DigestConfig(None, "", 7, 1800, False, 0, 4)

        return DigestConfig(
            delivery_chat_id=row[0],
            user_prefs_text=row[1] or "",
            threshold=int(row[2]),
            frequency_s=int(row[3]),
            paused=bool(row[4]),
            top_n=int(row[5] or 0),
            top_n_floor=int(row[6] or 4),
        )


async def save_config(path: Path, cfg: DigestConfig) -> None:
    """Upsert the config row."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        await db.execute(
            """
            UPDATE config
               SET delivery_chat_id = ?,
                   user_prefs_text  = ?,
                   threshold        = ?,
                   frequency_s      = ?,
                   paused           = ?,
                   top_n            = ?,
                   top_n_floor      = ?,
                   updated_at       = strftime('%s','now')
             WHERE id = 1
            """,
            (
                cfg.delivery_chat_id,
                cfg.user_prefs_text,
                cfg.threshold,
                cfg.frequency_s,
                1 if cfg.paused else 0,
                cfg.top_n,
                cfg.top_n_floor,
            ),
        )
        await db.commit()


async def list_tracked_channels(path: Path) -> list[dict[str, Any]]:
    """Return all tracked channels."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT channel_id, channel_title, channel_username, added_at FROM tracked_channels ORDER BY channel_title"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def add_tracked_channel(
    path: Path,
    channel_id: int,
    title: str,
    username: str | None = None,
) -> bool:
    """Add a channel to tracking. Returns True if newly added, False if already tracked."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        try:
            await db.execute(
                "INSERT INTO tracked_channels (channel_id, channel_title, channel_username) VALUES (?, ?, ?)",
                (channel_id, title, username),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_tracked_channel(path: Path, channel_id: int) -> bool:
    """Remove a channel from tracking. Returns True if removed."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        cur = await db.execute(
            "DELETE FROM tracked_channels WHERE channel_id = ?",
            (channel_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def is_channel_tracked(path: Path, channel_id: int) -> bool:
    """Check if a channel is tracked."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        cur = await db.execute(
            "SELECT 1 FROM tracked_channels WHERE channel_id = ? LIMIT 1",
            (channel_id,),
        )
        return (await cur.fetchone()) is not None


async def add_blocked_channel(
    path: Path,
    channel_id: int,
    title: str = "",
    username: str | None = None,
) -> bool:
    """Add a channel to the blocklist. Idempotent — returns True if newly added."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        try:
            await db.execute(
                "INSERT INTO blocked_channels (channel_id, channel_title, channel_username) VALUES (?, ?, ?)",
                (channel_id, title, username),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_blocked_channel(path: Path, channel_id: int) -> bool:
    """Remove a channel from the blocklist. Returns True if removed."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        cur = await db.execute(
            "DELETE FROM blocked_channels WHERE channel_id = ?",
            (channel_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def is_channel_blocked(path: Path, channel_id: int) -> bool:
    """Check if a channel is on the blocklist."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        cur = await db.execute(
            "SELECT 1 FROM blocked_channels WHERE channel_id = ? LIMIT 1",
            (channel_id,),
        )
        return (await cur.fetchone()) is not None


async def list_blocked_channels(path: Path) -> list[dict[str, Any]]:
    """Return all blocked channels."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT channel_id, channel_title, channel_username, blocked_at FROM blocked_channels ORDER BY blocked_at DESC"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def buffer_post(
    path: Path,
    channel_id: int,
    message_id: int,
    text: str,
) -> bool:
    """Add a post to the buffer. Returns True if newly buffered, False on duplicate."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        try:
            await db.execute(
                "INSERT INTO post_buffer (channel_id, message_id, received_at, text) VALUES (?, ?, ?, ?)",
                (channel_id, message_id, time.time(), text),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def drain_buffer(path: Path) -> list[dict[str, Any]]:
    """Return all buffered posts and clear the buffer atomically."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT p.id, p.channel_id, p.message_id, p.received_at, p.text,
                   t.channel_title, t.channel_username
              FROM post_buffer p
         LEFT JOIN tracked_channels t ON t.channel_id = p.channel_id
             ORDER BY p.received_at
            """
        )
        rows = await cur.fetchall()
        result = [dict(r) for r in rows]

        await db.execute("DELETE FROM post_buffer")
        await db.commit()
        return result


async def seen_recently(path: Path, post_hash: str, ttl_s: float = 86400) -> bool:
    """Check dedupe cache. Inserts the hash if unseen. Returns True if already seen within TTL."""
    import aiosqlite

    now = time.time()
    async with aiosqlite.connect(str(path)) as db:
        # Purge expired entries opportunistically
        await db.execute(
            "DELETE FROM dedupe_cache WHERE first_seen_at < ?",
            (now - ttl_s,),
        )

        cur = await db.execute(
            "SELECT 1 FROM dedupe_cache WHERE post_hash = ? LIMIT 1",
            (post_hash,),
        )
        row = await cur.fetchone()
        if row is not None:
            await db.commit()
            return True

        await db.execute(
            "INSERT INTO dedupe_cache (post_hash, first_seen_at) VALUES (?, ?)",
            (post_hash, now),
        )
        await db.commit()
        return False


async def record_cycle_stats(
    path: Path,
    cycle_start: float,
    cycle_end: float,
    msgs_scanned: int,
    msgs_delivered: int,
    llm_error: bool = False,
) -> None:
    """Insert a stats row for the completed cycle."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        await db.execute(
            """
            INSERT INTO stats (cycle_start, cycle_end, msgs_scanned, msgs_delivered, llm_error)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cycle_start, cycle_end, msgs_scanned, msgs_delivered, 1 if llm_error else 0),
        )
        await db.commit()


async def recent_stats(path: Path, since: float) -> dict[str, int]:
    """Aggregate stats since a given timestamp."""
    import aiosqlite

    async with aiosqlite.connect(str(path)) as db:
        cur = await db.execute(
            """
            SELECT COALESCE(SUM(msgs_scanned), 0)   AS scanned,
                   COALESCE(SUM(msgs_delivered), 0) AS delivered,
                   COUNT(*) AS cycles,
                   COALESCE(SUM(llm_error), 0)     AS errors
              FROM stats
             WHERE cycle_end >= ?
            """,
            (since,),
        )
        row = await cur.fetchone()
        return {
            "scanned": int(row[0] or 0),
            "delivered": int(row[1] or 0),
            "cycles": int(row[2] or 0),
            "errors": int(row[3] or 0),
        }
