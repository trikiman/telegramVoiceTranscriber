#!/usr/bin/env python3
"""Dry run: scan channels' OWN RECENT POSTS for VPN trial offers.

Unlike dry-run-finder.py (which reads only sponsored ad slots), this reads the
last N messages each channel POSTED. That's where a channel like "БЕСПЛАТНЫЙ ВПН"
actually advertises its 30-day free offer — in its feed, not its ad slots.

NO /start, NO mute, NO folder changes — read-only + LLM judging.

Usage:
    python scripts/dry-run-posts.py [max_channels] [posts_per_channel]
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient
from telethon.tl.types import (
    KeyboardButtonUrl,
    MessageEntityTextUrl,
    MessageEntityUrl,
)

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.judge import OfferJudge
from tg_voice_transcriber.finder.links import parse_tme_target
from tg_voice_transcriber.groq_client import GroqClient
from tg_voice_transcriber.llm_failover import FailoverChatClient
from tg_voice_transcriber.openrouter_client import OpenRouterClient

_TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/[^\s)\]]+"
    r"|tg://resolve\?[^\s)\]]+",
    re.IGNORECASE,
)


def _extract_links(msg) -> list[tuple[str, str | None]]:
    """Return (url, button_text) pairs found in a message's text and buttons."""
    found: list[tuple[str, str | None]] = []

    text = msg.message or ""

    # 1) Plain-text URLs and text_url entities
    for ent in msg.entities or []:
        if isinstance(ent, MessageEntityTextUrl) and ent.url:
            found.append((ent.url, None))
        elif isinstance(ent, MessageEntityUrl):
            seg = text[ent.offset : ent.offset + ent.length]
            found.append((seg, None))

    # 2) Regex sweep over raw text (catches bare t.me/... not marked as entities)
    for m in _TME_RE.finditer(text):
        found.append((m.group(0), None))

    # 3) Inline keyboard buttons (KeyboardButtonUrl carries a label + url)
    markup = getattr(msg, "reply_markup", None)
    if markup is not None:
        for row in getattr(markup, "rows", []) or []:
            for btn in getattr(row, "buttons", []) or []:
                if isinstance(btn, KeyboardButtonUrl) and btn.url:
                    found.append((btn.url, getattr(btn, "text", None)))

    return found


async def main() -> None:
    max_channels = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    posts_per_channel = int(sys.argv[2]) if len(sys.argv) > 2 else 15

    cfg = get_config()

    client = TelegramClient(
        str(cfg.finder_session_path),
        cfg.api_id,
        cfg.api_hash.get_secret_value(),
    )
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT AUTHORIZED — run scripts/login-finder.py first", file=sys.stderr)
        sys.exit(1)

    me = await client.get_me()
    print(f"Connected as: {me.first_name} {me.last_name or ''}")

    if cfg.groq_api_key is None and cfg.openrouter_api_keys is None:
        print("\n✗ No LLM key configured. Add TG_VOICE_GROQ_API_KEY to .env", file=sys.stderr)
        await client.disconnect()
        sys.exit(1)

    groq = None
    llm = None
    if cfg.groq_api_key is not None:
        groq = GroqClient(api_keys=cfg.groq_api_key.get_secret_value())
        groq.load()
        llm = groq
    if cfg.openrouter_api_keys:
        orc = OpenRouterClient(api_keys=cfg.openrouter_api_keys.get_secret_value())
        try:
            orc.load()
            llm = FailoverChatClient(primary=groq, fallback=orc, fallback_model=cfg.finder_fallback_model) if groq else orc
        except Exception:
            pass

    judge = OfferJudge(llm, model=cfg.finder_llm_model)

    channels = []
    async for dialog in client.iter_dialogs():
        if dialog.is_channel and not dialog.is_group:
            channels.append(dialog)
        if len(channels) >= max_channels:
            break

    print(f"Scanning {len(channels)} channels x {posts_per_channel} recent posts...\n")

    total_posts = 0
    judged_cache: dict[str, object] = {}
    good = []

    # Token-aware pacing to stay under Groq's 6000 TPM (8b free tier). Track
    # estimated tokens in a rolling 60s window; sleep before we'd exceed it.
    TPM_BUDGET = 5200  # headroom under 6000
    SYS_PROMPT_TOKENS = 480  # ~ system prompt cost per call
    token_window: deque[tuple[float, int]] = deque()

    async def _pace(est_tokens: int) -> None:
        now = time.time()
        while token_window and now - token_window[0][0] > 60:
            token_window.popleft()
        used = sum(t for _, t in token_window)
        if used + est_tokens > TPM_BUDGET and token_window:
            wait = 61 - (now - token_window[0][0])
            if wait > 0:
                print(f"    …pacing {wait:.0f}s (TPM {used}/{TPM_BUDGET})")
                await asyncio.sleep(wait)
        token_window.append((time.time(), est_tokens))


    for d in channels:
        posts_seen = 0
        try:
            async for msg in client.iter_messages(d.id, limit=posts_per_channel):
                if not msg.message:
                    continue
                posts_seen += 1
                total_posts += 1
                text = msg.message

                # Resolve the best deep-link target embedded in this post.
                link_target = None
                for url, btn_text in _extract_links(msg):
                    lt = parse_tme_target(url, button_text=btn_text)
                    if lt is not None and lt.is_bot:
                        link_target = lt
                        break
                    if lt is not None and link_target is None:
                        link_target = lt  # fall back to first resolvable target

                if len(text) < 15:
                    continue

                dedup_key = " ".join(text.lower().split())[:400]
                cached = judged_cache.get(dedup_key)
                if cached is not None:
                    continue  # already judged this exact post text
                # Offers live in the first lines; clamp to save tokens.
                judge_text = text[:900]
                est = SYS_PROMPT_TOKENS + len(judge_text) // 3
                await _pace(est)
                judged = await judge.judge_offer(
                    text=judge_text, channel_id=d.id, message_id=msg.id, link_target=link_target
                )
                judged_cache[dedup_key] = judged

                if judged is None:
                    continue
                if judged.is_good_trial and not judged.scam_suspected:
                    bot = judged.target_bot or "(no bot)"
                    print(f"  [{d.title[:24]}] ✅ {bot} | {judged.summary[:70]}")
                    good.append(judged)
        except Exception as exc:
            print(f"  [{d.title[:24]}] error: {exc}")
            continue

    print(f"\n=== POST SCAN RESULTS ===")
    print(f"Posts read: {total_posts}  |  unique judged: {len(judged_cache)}")
    print(f"Good offers found: {len(good)}")
    seen_bots = set()
    for g in good:
        key = g.target_bot or g.summary
        if key in seen_bots:
            continue
        seen_bots.add(key)
        tok = f"  (token: {g.start_param[:20]}...)" if g.start_param else ""
        days = f"[{g.trial_days}d]" if g.trial_days else ""
        print(f"  → {g.target_bot or '(no bot)'} {days}: {g.summary}{tok}")

    if groq:
        await groq.close()
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
