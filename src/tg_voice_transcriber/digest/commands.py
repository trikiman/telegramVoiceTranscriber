"""/digest command handlers, invoked from the user's Saved Messages."""

from __future__ import annotations

import re
import time
from pathlib import Path

import structlog
from telethon import TelegramClient, events

from tg_voice_transcriber.digest import db as digest_db
from tg_voice_transcriber.digest.scheduler import DigestScheduler

log = structlog.get_logger()

COMMAND_PATTERN = re.compile(r"^/digest(?:\s+(.*))?$", re.DOTALL)

HELP_TEXT = """📋 Digest commands

/digest setup            — configure delivery channel, preferences, threshold
/digest pause            — pause scheduled digests
/digest resume           — resume scheduled digests
/digest now              — send digest immediately (if any posts in buffer)
/digest channels         — list tracked channels
/digest prefs            — show current preferences
/digest prefs <text>     — update preferences
/digest threshold <N>    — set relevance threshold (1-10)
/digest frequency <min>  — set digest frequency in minutes (min 5)
/digest stats            — show last-7-day stats
/digest unsub @name      — stop tracking a channel
/digest help             — this message
"""


def register_command_handlers(
    client: TelegramClient,
    db_path: Path,
    scheduler: DigestScheduler,
) -> None:
    """Register /digest command handlers on the user's own (outgoing) messages in Saved Messages."""

    @client.on(events.NewMessage(outgoing=True, pattern=COMMAND_PATTERN))
    async def on_digest_command(event: events.NewMessage.Event) -> None:
        # Only respond in self-chat (Saved Messages)
        me = await client.get_me()
        if event.chat_id != me.id:
            return

        raw = (event.message.message or "").strip()
        match = COMMAND_PATTERN.match(raw)
        if not match:
            return

        args = (match.group(1) or "").strip()
        parts = args.split(maxsplit=1) if args else []
        subcmd = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""

        try:
            reply = await _dispatch(client, db_path, scheduler, subcmd, rest)
        except Exception as exc:
            log.error("digest_command_failed", subcmd=subcmd, exc_info=True)
            reply = f"❌ command failed: {exc}"

        await event.message.edit(reply)


async def _dispatch(
    client: TelegramClient,
    db_path: Path,
    scheduler: DigestScheduler,
    subcmd: str,
    rest: str,
) -> str:
    if subcmd in ("", "help"):
        return HELP_TEXT

    if subcmd == "setup":
        return await _cmd_setup(client, db_path, rest)

    if subcmd == "pause":
        return await _cmd_pause(db_path)

    if subcmd == "resume":
        return await _cmd_resume(db_path)

    if subcmd == "now":
        scheduler.wake_now()
        return "⏱ Digest cycle triggered. Check your delivery channel in a moment."

    if subcmd == "channels":
        return await _cmd_channels(db_path)

    if subcmd == "prefs":
        return await _cmd_prefs(db_path, rest)

    if subcmd == "threshold":
        return await _cmd_threshold(db_path, rest)

    if subcmd == "frequency":
        return await _cmd_frequency(db_path, rest)

    if subcmd == "stats":
        return await _cmd_stats(db_path)

    if subcmd == "unsub":
        return await _cmd_unsub(client, db_path, rest)

    return f"❓ unknown subcommand: {subcmd}\n\n{HELP_TEXT}"


async def _cmd_setup(
    client: TelegramClient,
    db_path: Path,
    rest: str,
) -> str:
    """Setup expects:  /digest setup <delivery_chat_ref> | <prefs text>

    delivery_chat_ref can be @channelname, a t.me link, or a numeric chat_id.
    """
    if not rest or "|" not in rest:
        return (
            "Usage:\n"
            "/digest setup <delivery_ref> | <preferences text>\n\n"
            "Example:\n"
            "/digest setup @mydigestchannel | Интересно: ИИ, Python, Linux. Неинтересно: крипта, мемы.\n\n"
            "The delivery channel should be a private channel you create and add yourself as admin.\n"
            "Paste its @username, t.me link, or numeric chat_id."
        )

    delivery_ref, prefs_text = [part.strip() for part in rest.split("|", 1)]
    if not delivery_ref or not prefs_text:
        return "❌ Both delivery_ref and preferences text are required (separated by |)."

    # Resolve the delivery channel
    try:
        entity = await client.get_entity(delivery_ref)
    except Exception as exc:
        return f"❌ Could not resolve delivery channel '{delivery_ref}': {exc}"

    delivery_chat_id = entity.id
    # Normalize to full -100... form for channels/supergroups
    if hasattr(entity, "megagroup") or hasattr(entity, "broadcast"):
        delivery_chat_id = int(f"-100{entity.id}")

    # Test: can we send there?
    try:
        test_msg = await client.send_message(
            delivery_chat_id,
            "✅ Digest delivery confirmed. Future digests will appear here.",
        )
    except Exception as exc:
        return f"❌ Cannot post to delivery channel: {exc}\n\nMake sure you've added yourself as admin of that channel."

    cfg = await digest_db.load_config(db_path)
    cfg.delivery_chat_id = delivery_chat_id
    cfg.user_prefs_text = prefs_text
    cfg.paused = False
    await digest_db.save_config(db_path, cfg)

    tracked = await digest_db.list_tracked_channels(db_path)

    return (
        f"✅ Setup complete.\n"
        f"Delivery: {delivery_ref}\n"
        f"Threshold: {cfg.threshold}/10\n"
        f"Frequency: every {cfg.frequency_s // 60} min\n"
        f"Tracked channels: {len(tracked)} (auto-added as new channel posts arrive)\n"
        f"Preferences: {prefs_text[:100]}{'…' if len(prefs_text) > 100 else ''}"
    )


async def _cmd_pause(db_path: Path) -> str:
    cfg = await digest_db.load_config(db_path)
    if cfg.paused:
        return "ℹ Digest already paused."
    cfg.paused = True
    await digest_db.save_config(db_path, cfg)
    return "⏸ Digest paused. Use `/digest resume` to re-enable."


async def _cmd_resume(db_path: Path) -> str:
    cfg = await digest_db.load_config(db_path)
    if not cfg.paused:
        return "ℹ Digest already running."
    cfg.paused = False
    await digest_db.save_config(db_path, cfg)
    return "▶ Digest resumed."


async def _cmd_channels(db_path: Path) -> str:
    channels = await digest_db.list_tracked_channels(db_path)
    if not channels:
        return "ℹ No tracked channels yet. Join some channels and they'll be auto-added when they post."

    lines = [f"📎 {len(channels)} tracked channel(s):"]
    for c in channels[:50]:
        tag = f"@{c['channel_username']}" if c["channel_username"] else c["channel_title"]
        lines.append(f"• {tag}")
    if len(channels) > 50:
        lines.append(f"… and {len(channels) - 50} more")
    return "\n".join(lines)


async def _cmd_prefs(db_path: Path, rest: str) -> str:
    cfg = await digest_db.load_config(db_path)
    if not rest.strip():
        return f"📝 Current preferences:\n\n{cfg.user_prefs_text or '(not set)'}"

    cfg.user_prefs_text = rest.strip()
    await digest_db.save_config(db_path, cfg)
    return f"✅ Preferences updated ({len(cfg.user_prefs_text)} chars)."


async def _cmd_threshold(db_path: Path, rest: str) -> str:
    try:
        value = int(rest.strip())
    except ValueError:
        return "❌ Usage: `/digest threshold <1-10>`"
    if not 1 <= value <= 10:
        return "❌ Threshold must be between 1 and 10."

    cfg = await digest_db.load_config(db_path)
    cfg.threshold = value
    await digest_db.save_config(db_path, cfg)
    return f"✅ Threshold set to {value}/10."


async def _cmd_frequency(db_path: Path, rest: str) -> str:
    try:
        minutes = int(rest.strip())
    except ValueError:
        return "❌ Usage: `/digest frequency <minutes>`"
    if minutes < 5:
        return "❌ Minimum frequency is 5 minutes."
    if minutes > 1440:
        return "❌ Maximum frequency is 1440 minutes (24h)."

    cfg = await digest_db.load_config(db_path)
    cfg.frequency_s = minutes * 60
    await digest_db.save_config(db_path, cfg)
    return f"✅ Frequency set to every {minutes} min."


async def _cmd_stats(db_path: Path) -> str:
    seven_days_ago = time.time() - 7 * 86400
    s = await digest_db.recent_stats(db_path, since=seven_days_ago)
    tracked = await digest_db.list_tracked_channels(db_path)

    noise_ratio = 0.0
    if s["scanned"] > 0:
        noise_ratio = (1 - s["delivered"] / s["scanned"]) * 100

    return (
        "📊 Digest stats — last 7 days\n\n"
        f"Scanned: {s['scanned']} posts\n"
        f"Delivered: {s['delivered']} ({100 - noise_ratio:.1f}%)\n"
        f"Filtered as noise: {noise_ratio:.1f}%\n"
        f"Cycles: {s['cycles']}\n"
        f"LLM errors: {s['errors']}\n"
        f"Tracked channels: {len(tracked)}"
    )


async def _cmd_unsub(
    client: TelegramClient,
    db_path: Path,
    rest: str,
) -> str:
    ref = rest.strip().lstrip("@")
    if not ref:
        return "❌ Usage: `/digest unsub @channelname`"

    try:
        entity = await client.get_entity(ref)
    except Exception as exc:
        return f"❌ Could not resolve @{ref}: {exc}"

    channel_id = entity.id
    if hasattr(entity, "megagroup") or hasattr(entity, "broadcast"):
        channel_id = int(f"-100{entity.id}")

    removed = await digest_db.remove_tracked_channel(db_path, channel_id)
    if removed:
        return f"✅ Unsubscribed from @{ref}. Future posts will be ignored."
    return f"ℹ @{ref} was not being tracked."
