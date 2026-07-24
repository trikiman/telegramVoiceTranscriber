#!/usr/bin/env python3
"""Probe discovered VPN bots: /start each, read welcome + menu, judge, mute.

This mines the source the post/ad scans miss — the bot's OWN welcome screen,
where the real trial terms live (the @Ultaclub lesson: ad copy lies, the live
bot tells the truth). For each bot we:
  1. /start it (finder account only)
  2. read the welcome message text + inline/reply button labels
  3. run the offer judge over (text + button labels)
  4. mute it (user rule: always mute)

Read-only w.r.t. offers — we do NOT claim/subscribe, just inspect + judge.
Serial on the single finder session. Human-like delays.

Usage:
    python scripts/probe-bots.py                 # all bots from discover.log
    python scripts/probe-bots.py @g5vpnbot @foo   # explicit list
"""

from __future__ import annotations

import asyncio
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient
from telethon.tl.functions.contacts import UnblockRequest

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.judge import OfferJudge
from tg_voice_transcriber.finder.mute import mute_peer
from tg_voice_transcriber.groq_client import GroqClient
from tg_voice_transcriber.llm_failover import FailoverChatClient
from tg_voice_transcriber.openrouter_client import OpenRouterClient

DISCOVER_LOG = Path(__file__).resolve().parent.parent / ".local" / "discover.log"
OUT_LOG = Path(__file__).resolve().parent.parent / ".local" / "probe-bots.log"


def _bots_from_log() -> list[str]:
    if not DISCOVER_LOG.exists():
        return []
    text = DISCOVER_LOG.read_text(encoding="utf-8", errors="ignore")
    # Only the BOTS section.
    idx = text.upper().find("BOTS")
    section = text[idx:] if idx != -1 else text
    names = re.findall(r"@([A-Za-z0-9_]+)", section)
    # de-dup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        low = n.lower()
        if low in seen or low == "vless":  # 'vless' is a keyword, not a bot
            continue
        seen.add(low)
        out.append(n)
    return out


def _collect_buttons(msg) -> list[str]:
    labels: list[str] = []
    markup = getattr(msg, "reply_markup", None)
    if not markup:
        return labels
    for row in getattr(markup, "rows", []) or []:
        for btn in getattr(row, "buttons", []) or []:
            t = getattr(btn, "text", None)
            if t:
                labels.append(t)
    return labels


def _build_llm(cfg):
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
            llm = (
                FailoverChatClient(
                    primary=groq, fallback=orc,
                    fallback_model=cfg.finder_fallback_model,
                )
                if groq
                else orc
            )
        except Exception:
            pass
    return groq, llm


async def main() -> None:
    cfg = get_config()

    explicit = [a.lstrip("@") for a in sys.argv[1:] if not a.startswith("-")]
    bots = explicit or _bots_from_log()
    if not bots:
        print("No bots to probe (empty discover.log BOTS section).", file=sys.stderr)
        sys.exit(1)

    client = TelegramClient(
        str(cfg.finder_session_path),
        cfg.api_id,
        cfg.api_hash.get_secret_value(),
    )
    await client.connect()
    if not await client.is_user_authorized():
        print("Finder session NOT AUTHORIZED", file=sys.stderr)
        sys.exit(1)

    if cfg.groq_api_key is None and cfg.openrouter_api_keys is None:
        print("No LLM key configured.", file=sys.stderr)
        sys.exit(1)

    groq, llm = _build_llm(cfg)
    judge = OfferJudge(llm, model=cfg.finder_llm_model)

    me = await client.get_me()
    print(f"Finder account: {me.first_name}")
    print(f"Probing {len(bots)} bots (welcome-message judge, then mute)\n")

    good: list[tuple[str, object]] = []
    lines: list[str] = []

    for i, uname in enumerate(bots, 1):
        try:
            bot = await client.get_entity(uname)
        except Exception as exc:
            print(f"  [{i}/{len(bots)}] @{uname}: resolve failed ({exc})")
            continue

        try:
            await client(UnblockRequest(id=bot))
        except Exception:
            pass

        try:
            await client.send_message(bot, "/start")
        except Exception as exc:
            print(f"  [{i}/{len(bots)}] @{uname}: /start failed ({exc})")
            await mute_peer(client, bot)
            continue

        await asyncio.sleep(random.uniform(3.0, 5.0))

        msgs = await client.get_messages(bot, limit=3)
        welcome = next((m for m in msgs if (m.message or "").strip()), None)
        text = (welcome.message if welcome else "") or ""
        buttons = _collect_buttons(welcome) if welcome else []

        judged = None
        if len(text) >= 15:
            judged = await judge.judge_offer(
                text=text, channel_id=0, buttons=buttons
            )

        # Always mute (user rule).
        await mute_peer(client, bot)

        if judged is None:
            verdict = "?? (no/short welcome or LLM skip)"
            summary = text[:70].replace("\n", " ")
        else:
            is_good = judged.is_good_trial and not judged.scam_suspected
            verdict = "✅ GOOD" if is_good else "❌ skip"
            summary = judged.summary[:80]
            if is_good:
                good.append((uname, judged))

        line = f"  [{i}/{len(bots)}] @{uname}: {verdict} | {summary}"
        print(line)
        lines.append(line)

        await asyncio.sleep(random.uniform(4.0, 8.0))  # human-like, rate-safe

    print("\n=== PROBE RESULTS ===")
    print(f"Bots probed: {len(bots)}  |  good offers: {len(good)}")
    for uname, j in good:
        days = f"{j.trial_days}d" if j.trial_days else "?d"
        print(f"  → @{uname} [{days}]: {j.summary}")

    OUT_LOG.write_text(
        "\n".join(lines)
        + "\n\n=== GOOD ===\n"
        + "\n".join(
            f"@{u} [{(j.trial_days or '?')}d]: {j.summary}" for u, j in good
        ),
        encoding="utf-8",
    )
    print(f"\nFull log → {OUT_LOG}")

    if groq:
        await groq.close()
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
