#!/usr/bin/env python3
"""Harvest free MTProto proxies from a subscribe-gate proxy bot (e.g. @TProxyRobot).

Automates the manual loop the user does by hand:
  press "Получить прокси" -> read proxy -> press "Ещё один" -> repeat.

Real flow discovered for @TProxyRobot:
  1. /start shows a subscribe gate: join N channels, then a `check_join`
     inline callback.  We join+mute those channels (user rule: always mute,
     finder account only) and click check_join.
  2. "Получить прокси" is a REPLY-KEYBOARD button (KeyboardButton, no data/url)
     -> you "press" it by sending its label as a text message.
  3. Each proxy reply carries a "Connect" inline URL button whose url is the
     canonical t.me/proxy or tg://proxy link -> that's what we store.
  4. "Ещё один" is a real inline callback button -> .click() it for the next
     proxy.  (Sending "Получить прокси" again also works.)

Everything runs on the finder (non-main) account.  Proxies are appended to
.local/proxies.txt (deduped).

Usage:
    python scripts/harvest-proxies.py TProxyRobot 8
    python scripts/harvest-proxies.py TProxyRobot 8 --dry-run
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.contacts import UnblockRequest

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.mute import mute_peer

# --- tunables -------------------------------------------------------------
GET_LABEL = "Получить прокси"          # reply-keyboard button -> send as text
MORE_DATA = b"another"                  # inline "Ещё один" callback (fallback below)
MORE_LABELS = ("ещё один", "еще один", "another", "next")
CHECK_JOIN_DATA = b"check_join"
WAIT_S = 3.5                            # pause after each bot interaction

PROXY_ROOT = Path(__file__).resolve().parent.parent / ".local"
PROXY_FILE = PROXY_ROOT / "proxies.txt"

# A proxy "Connect" button url looks like:
#   https://t.me/proxy?server=...&port=...&secret=...
#   tg://proxy?server=...&port=...&secret=...
_PROXY_URL_RE = re.compile(
    r"(?:tg://proxy|https?://t\.me/proxy)\?server=[^&\s]+&port=\d+&secret=[0-9a-fA-F]+",
    re.IGNORECASE,
)


def _normalise(url: str) -> str:
    """Return a canonical tg://proxy?... form for dedup/storage."""
    m = re.search(r"server=([^&]+)&port=(\d+)&secret=([0-9a-fA-F]+)", url, re.IGNORECASE)
    if not m:
        return url
    server, port, secret = m.group(1), m.group(2), m.group(3)
    return f"tg://proxy?server={server}&port={port}&secret={secret}"


def _extract_proxy(message) -> str | None:
    """Pull the proxy link out of a bot message (prefer the Connect button)."""
    markup = getattr(message, "reply_markup", None)
    if markup:
        for row in getattr(markup, "rows", []) or []:
            for btn in getattr(row, "buttons", []) or []:
                url = getattr(btn, "url", None)
                if url and _PROXY_URL_RE.search(url):
                    return _normalise(_PROXY_URL_RE.search(url).group(0))
    # Fallback: search the text body.
    text = getattr(message, "message", "") or ""
    m = _PROXY_URL_RE.search(text)
    if m:
        return _normalise(m.group(0))
    return None


def _find_more_button(message):
    """Return (message, data) for an 'Ещё один'/'another' inline callback."""
    markup = getattr(message, "reply_markup", None)
    if not markup:
        return None
    for row in getattr(markup, "rows", []) or []:
        for btn in getattr(row, "buttons", []) or []:
            data = getattr(btn, "data", None)
            label = (getattr(btn, "text", "") or "").strip().lower()
            if data and any(lbl in label for lbl in MORE_LABELS):
                return data
    return None


async def _pass_gate(client, bot) -> None:
    """Join+mute gate channels and click check_join, if a gate is present."""
    await client.send_message(bot, "/start")
    await asyncio.sleep(WAIT_S)

    msgs = await client.get_messages(bot, limit=3)
    gate_urls: list[str] = []
    check_msg = None
    for m in msgs:
        markup = getattr(m, "reply_markup", None)
        if not markup:
            continue
        for row in getattr(markup, "rows", []) or []:
            for btn in getattr(row, "buttons", []) or []:
                if getattr(btn, "data", None) == CHECK_JOIN_DATA:
                    check_msg = m
                url = getattr(btn, "url", None)
                if url and "t.me/" in url and "proxy" not in url:
                    gate_urls.append(url)

    if not check_msg:
        return  # no gate

    # Join + mute each gate channel.
    for url in gate_urls:
        uname = url.rstrip("/").split("/")[-1]
        try:
            ent = await client.get_entity(uname)
            await client(JoinChannelRequest(ent))
            await mute_peer(client, ent)
            print(f"  gate: joined+muted @{uname}")
        except Exception as exc:
            print(f"  gate: @{uname} failed: {exc}")
        await asyncio.sleep(2.5)

    try:
        await check_msg.click(data=CHECK_JOIN_DATA)
        print("  gate: clicked check_join")
    except Exception as exc:
        print(f"  gate: check_join click failed: {exc}")
    await asyncio.sleep(WAIT_S)


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: harvest-proxies.py <bot_username> [count] [--dry-run]", file=sys.stderr)
        sys.exit(1)
    bot_username = sys.argv[1].lstrip("@")
    count = 8
    dry_run = "--dry-run" in sys.argv
    for a in sys.argv[2:]:
        if a.isdigit():
            count = int(a)

    cfg = get_config()
    client = TelegramClient(
        str(cfg.finder_session_path),
        cfg.api_id,
        cfg.api_hash.get_secret_value(),
    )
    await client.connect()
    if not await client.is_user_authorized():
        print("Finder session NOT AUTHORIZED", file=sys.stderr)
        sys.exit(1)

    me = await client.get_me()
    print(f"Finder account: {me.first_name}")
    print(f"Harvesting up to {count} proxies from @{bot_username}"
          + (" (DRY RUN)" if dry_run else ""))

    bot = await client.get_entity(bot_username)
    try:
        await client(UnblockRequest(id=bot))
    except Exception:
        pass

    await _pass_gate(client, bot)

    # Press "Получить прокси" (reply-keyboard button = send its text).
    await client.send_message(bot, GET_LABEL)
    await asyncio.sleep(WAIT_S)

    harvested: list[str] = []
    seen: set[str] = set()

    for i in range(count):
        latest = (await client.get_messages(bot, limit=1))[0]
        proxy = _extract_proxy(latest)
        if proxy and proxy not in seen:
            seen.add(proxy)
            harvested.append(proxy)
            print(f"  [{len(harvested)}] {proxy}")
        elif proxy:
            print("  (duplicate proxy, skipping)")
        else:
            print("  (no proxy in latest message)")

        if len(harvested) >= count:
            break

        # Ask for the next one: prefer the "Ещё один" inline callback.
        more = _find_more_button(latest)
        if more:
            try:
                await latest.click(data=more)
            except Exception as exc:
                print(f"  'Ещё один' click failed ({exc}); resending {GET_LABEL!r}")
                await client.send_message(bot, GET_LABEL)
        else:
            await client.send_message(bot, GET_LABEL)
        await asyncio.sleep(WAIT_S)

    # Mute the bot too (user rule).
    await mute_peer(client, bot)

    print(f"\nHarvested {len(harvested)} unique proxies.")

    if harvested and not dry_run:
        PROXY_ROOT.mkdir(parents=True, exist_ok=True)
        existing = set()
        if PROXY_FILE.exists():
            existing = {
                ln.strip()
                for ln in PROXY_FILE.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            }
        new = [p for p in harvested if p not in existing]
        if new:
            with PROXY_FILE.open("a", encoding="utf-8") as fh:
                for p in new:
                    fh.write(p + "\n")
        print(f"Wrote {len(new)} new proxies to {PROXY_FILE} "
              f"({len(existing)} already present).")
    elif dry_run:
        print("(dry run — nothing written)")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
