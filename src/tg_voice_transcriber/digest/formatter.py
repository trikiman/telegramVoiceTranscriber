"""Format scored posts into delivery-ready digest message(s)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from tg_voice_transcriber.digest.scorer import ScoredPost

# Telegram message length cap
MAX_MESSAGE_LENGTH = 4096


def format_digest(
    scored: list[ScoredPost],
    *,
    threshold: int,
    window_start: float,
    window_end: float,
    top_n: int | None = None,
) -> list[str]:
    """Format scored posts into one or more digest messages.

    Two modes:
    - Threshold mode (top_n=None): include posts with score >= threshold;
      empty list if nothing qualifies.
    - Top-N mode (top_n=5): always include the top N posts by score regardless
      of threshold. Returns empty only if scored is empty.
    """
    if not scored:
        return []

    if top_n is not None and top_n > 0:
        # Top-N mode: take highest-scoring posts (ties broken by original order)
        relevant = sorted(scored, key=lambda p: -p.score)[:top_n]
        if not relevant:
            return []
        header = _format_header(window_start, window_end, len(relevant), len(scored), top_n_mode=True)
    else:
        relevant = [p for p in scored if p.score >= threshold]
        if not relevant:
            return []
        header = _format_header(window_start, window_end, len(relevant), len(scored), top_n_mode=False)

    # Group by channel
    by_channel: dict[tuple[str, str | None], list[ScoredPost]] = defaultdict(list)
    for p in relevant:
        key = (p.channel_title, p.channel_username)
        by_channel[key].append(p)

    # Sort channels alphabetically by title, posts within channel by descending score
    sorted_channels = sorted(by_channel.items(), key=lambda kv: kv[0][0].lower())

    body_parts = []
    for (title, username), posts in sorted_channels:
        body_parts.append(_format_channel_block(title, username, posts))

    full = header + "\n\n" + "\n\n".join(body_parts)

    # Split into chunks if too long
    return _split_message(full)


def _format_header(start_ts: float, end_ts: float, delivered: int, scanned: int, *, top_n_mode: bool) -> str:
    start = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%H:%M")
    end = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%H:%M")
    if top_n_mode:
        return f"📋 Digest — {start} to {end} (top {delivered} of {scanned} scanned)"
    return f"📋 Digest — {start} to {end} ({delivered} of {scanned} relevant)"


def _format_channel_block(
    title: str,
    username: str | None,
    posts: list[ScoredPost],
) -> str:
    # Sort posts by descending score (most relevant first)
    sorted_posts = sorted(posts, key=lambda p: -p.score)
    header = f"📎 {title}" if not username else f"📎 @{username}"

    lines = [header]
    for p in sorted_posts:
        link = _build_post_link(p)
        summary = p.summary or _fallback_summary(p.original_text)

        # Build an optional deal badge
        badge = _format_deal_badge(p)
        score_label = f"[{p.score}/10]"
        if badge:
            lines.append(f"▸ {score_label} {badge} {summary}")
        else:
            lines.append(f"▸ {score_label} {summary}")

        if link:
            # Prefix "↗ source" makes it visually a "verify/context" affordance,
            # not the primary path to info (the summary already has everything).
            lines.append(f"  ↗ source: {link}")
    return "\n".join(lines)


def _format_deal_badge(post: ScoredPost) -> str:
    """Build a deal/scam prefix badge if the post is flagged."""
    if post.scam_suspected:
        return "⚠️ suspected scam —"
    if post.is_deal:
        if post.deal_value_usd and post.deal_value_usd >= 20:
            return f"💰 ~${post.deal_value_usd} free —"
        if post.deal_value_usd:
            return f"🎁 ~${post.deal_value_usd} deal —"
        return "🎁 free —"
    return ""


def _build_post_link(post: ScoredPost) -> str:
    """Build a t.me link to the original post."""
    if post.channel_username:
        return f"https://t.me/{post.channel_username}/{post.message_id}"
    # Private channel / no username → use numeric link form
    # Telethon gives chat_id as -100XXXXXXXXXX for channels; strip the -100 prefix for public links
    cid = post.channel_id
    if cid < 0:
        cid_abs = abs(cid)
        s = str(cid_abs)
        if s.startswith("100"):
            s = s[3:]
        return f"https://t.me/c/{s}/{post.message_id}"
    return ""


def _fallback_summary(text: str) -> str:
    """If the LLM didn't produce a summary, use the first 100 chars of the post."""
    clean = (text or "").strip().replace("\n", " ")
    if len(clean) <= 100:
        return clean
    return clean[:97].rstrip() + "…"


def _split_message(text: str) -> list[str]:
    """Split a long digest message at natural boundaries (channel blocks)."""
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    # Split at blank lines (between channel blocks)
    blocks = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= MAX_MESSAGE_LENGTH:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # Block itself might still be too long — hard-split if so
            while len(block) > MAX_MESSAGE_LENGTH:
                chunks.append(block[:MAX_MESSAGE_LENGTH])
                block = block[MAX_MESSAGE_LENGTH:]
            current = block
    if current:
        chunks.append(current)
    return chunks
