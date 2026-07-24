#!/usr/bin/env python3
"""Dry run: scan channels for sponsored ads, judge them, print results.

NO /start, NO mute, NO folder changes — read-only + LLM judging.

Usage:
    python scripts/dry-run-finder.py [max_channels]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.judge import OfferJudge
from tg_voice_transcriber.finder.links import parse_tme_target
from tg_voice_transcriber.finder.sponsored import SponsoredMessageFetcher
from tg_voice_transcriber.groq_client import GroqClient
from tg_voice_transcriber.llm_failover import FailoverChatClient
from tg_voice_transcriber.openrouter_client import OpenRouterClient


async def main() -> None:
    max_channels = int(sys.argv[1]) if len(sys.argv) > 1 else 8

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

    # LLM client — needs at least one of Groq / OpenRouter
    if cfg.groq_api_key is None and cfg.openrouter_api_keys is None:
        print(
            "\n✗ No LLM key configured. Add TG_VOICE_GROQ_API_KEY to .env\n"
            "  (get a free key at https://console.groq.com → API Keys)",
            file=sys.stderr,
        )
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
    fetcher = SponsoredMessageFetcher(client)

    # Collect broadcast channels
    channels = []
    async for dialog in client.iter_dialogs():
        if dialog.is_channel and not dialog.is_group:
            channels.append(dialog)
        if len(channels) >= max_channels:
            break

    print(f"Scanning {len(channels)} channels for sponsored ads...\n")

    total_ads = 0
    good = []
    # Dedup: the SAME ad repeats across nearly every channel (VPNPort, MTS, ...).
    # Judging identical text N times wastes the daily token budget and trips the
    # 8b 6000 TPM limit. Judge each unique ad ONCE, keyed by url|normalized-text.
    judged_cache: dict[str, object] = {}
    judged_count = 0

    for d in channels:
        try:
            ads = await fetcher.fetch_sponsored_messages(d.id)
        except Exception as exc:
            print(f"  [{d.title[:30]}] fetch error: {exc}")
            continue

        print(f"  [{d.title[:30]}] {len(ads)} ad(s)")
        for ad in ads:
            total_ads += 1
            text = ad["message"]
            # Deterministically resolve the deep-link target (bot + start token)
            link_target = parse_tme_target(
                ad.get("url"), button_text=ad.get("button_text")
            )
            # Include the ad title as context — offer text is often just in the title
            judge_text = text or ""
            title = ad.get("title")
            if title:
                judge_text = f"{title}\n{judge_text}".strip()
            if not judge_text or len(judge_text) < 15:
                continue

            # Dedup key: prefer the ad URL (stable per campaign); fall back to
            # the normalized offer text. If we've seen it, reuse the verdict —
            # no LLM call.
            dedup_key = (ad.get("url") or "").strip().lower() or " ".join(
                judge_text.lower().split()
            )
            cached = judged_cache.get(dedup_key)
            if cached is not None:
                judged = cached
                dup_marker = " (dup)"
            else:
                judged = await judge.judge_offer(
                    text=judge_text, channel_id=d.id, link_target=link_target
                )
                judged_cache[dedup_key] = judged
                judged_count += 1
                dup_marker = ""
                await asyncio.sleep(1.0)  # gentle pacing under the 8b TPM limit

            if judged is None:
                print(f"    - LLM failed on: {judge_text[:60]}...")
                continue
            verdict = "✅ GOOD" if (judged.is_good_trial and not judged.scam_suspected) else "❌ skip"
            bot = judged.target_bot or "(no bot)"
            print(f"    {verdict}{dup_marker} | {bot} | {judged.summary[:80]}")
            if judged.is_good_trial and not judged.scam_suspected:
                good.append(judged)

    print(f"\n=== DRY RUN RESULTS ===")
    print(f"Ads seen: {total_ads}  |  unique judged (LLM calls): {judged_count}")
    print(f"Good offers the finder WOULD collect: {len(good)}")
    # Dedup good offers by bot so the same campaign across channels lists once.
    seen_bots = set()
    for g in good:
        key = g.target_bot or g.summary
        if key in seen_bots:
            continue
        seen_bots.add(key)
        tok = f"  (start token: {g.start_param[:24]}...)" if g.start_param else ""
        print(f"  → {g.target_bot or '(no bot extracted)'}: {g.summary}{tok}")

    await groq.close()
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
