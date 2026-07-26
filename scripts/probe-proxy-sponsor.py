#!/usr/bin/env python3
"""Probe the PROXY-SPONSOR ad inventory (help.getPromoData).

Connecting Telegram through an MTProto proxy lets the proxy operator pin a
"sponsored" channel to the top of the chat list. That inventory is completely
separate from the per-channel sponsored ads we already scan — and it is
invisible unless the client is actually connected THROUGH a proxy. Connected
directly, help.getPromoData always returns PromoDataEmpty, which is why this
source has never produced anything.

This script connects via each harvested proxy in .local/proxies.txt, asks for
the promo data, and reports what (if anything) that proxy is promoting. It is
a READ-ONLY diagnostic: it never /starts, mutes, or files anything.

RESULT (2026-07-27): 0/7 proxies connected — and the blocker is the library,
not the proxies. Every harvested proxy is an ``ee`` (faketls) secret,
domain-fronted through rutube.ru / ozon.ru / browser.yandex.ru / arixo.shop.
Telethon does not implement faketls: normalize_secret() drops the domain with
the comment "until domain support is added" and then speaks plain MTProto to a
proxy that is waiting for a TLS ClientHello, so the connection just times out.
The official client shows these same proxies as Available (~53 ms) because it
does implement faketls. Plain/``dd`` secrets would work here, but public ones
are largely extinct — faketls is what survives blocking. So this source stays
dark unless faketls is implemented at the protocol level. Kept as the record
of that finding, and it will start working the day a non-faketls proxy is
harvested.

Trade-off, stated plainly: this routes the finder account's connection through
a proxy run by an unknown operator. MTProto proxies relay encrypted traffic
and do not hold your auth key, but the operator does see that your IP is
talking to Telegram. Runs on the finder account only, never the main one.

Usage:
    python scripts/probe-proxy-sponsor.py            # try each proxy in turn
    python scripts/probe-proxy-sponsor.py --limit 3  # only the first 3
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient
from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate

from tg_voice_transcriber.config import get_config

PROXY_FILE = Path(__file__).resolve().parent.parent / ".local" / "proxies.txt"

_PROXY_RE = re.compile(
    r"server=(?P<server>[^&\s]+)&port=(?P<port>\d+)&secret=(?P<secret>[0-9a-fA-F]+)"
)


def _load_proxies() -> list[tuple[str, int, str]]:
    if not PROXY_FILE.exists():
        return []
    out: list[tuple[str, int, str]] = []
    for line in PROXY_FILE.read_text(encoding="utf-8").splitlines():
        m = _PROXY_RE.search(line.strip())
        if m:
            out.append((m["server"], int(m["port"]), m["secret"]))
    return out


async def _probe(cfg, proxy: tuple[str, int, str]) -> list[str]:
    """Connect through one proxy and report what it promotes."""
    server, port, secret = proxy
    client = TelegramClient(
        str(cfg.finder_session_path),
        cfg.api_id,
        cfg.api_hash.get_secret_value(),
        connection=ConnectionTcpMTProxyRandomizedIntermediate,
        proxy=(server, port, secret),
        timeout=15,
    )

    findings: list[str] = []
    try:
        await asyncio.wait_for(client.connect(), timeout=25)
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ {server}:{port} — connect failed ({type(exc).__name__})")
        with contextlib.suppress(Exception):
            await client.disconnect()
        return findings

    try:
        if not await client.is_user_authorized():
            print(f"  ✗ {server}:{port} — session not authorized through this proxy")
            return findings

        from telethon.tl.functions.help import GetPromoDataRequest

        promo = await client(GetPromoDataRequest())
        kind = type(promo).__name__
        if kind == "PromoDataEmpty":
            print(f"  · {server}:{port} — connected, but no promo inventory")
            return findings

        print(f"  ★ {server}:{port} — {kind}")
        for attr in ("psa_type", "psa_message"):
            val = getattr(promo, attr, None)
            if val:
                print(f"      {attr}: {str(val)[:120]}")
        for chat in getattr(promo, "chats", []) or []:
            uname = getattr(chat, "username", None)
            title = getattr(chat, "title", None)
            print(f"      PROMOTED: @{uname} | {title}")
            if uname:
                findings.append(uname)
        for user in getattr(promo, "users", []) or []:
            uname = getattr(user, "username", None)
            if uname:
                is_bot = getattr(user, "bot", False)
                print(f"      PROMOTED {'BOT' if is_bot else 'USER'}: @{uname}")
                findings.append(uname)
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()
    return findings


async def main() -> int:
    ap = argparse.ArgumentParser(description="Probe proxy-sponsored ad inventory")
    ap.add_argument("--limit", type=int, default=0, help="Only try the first N proxies.")
    args = ap.parse_args()

    cfg = get_config()
    proxies = _load_proxies()
    if not proxies:
        print(f"No proxies in {PROXY_FILE} — run scripts/harvest-proxies.py first.",
              file=sys.stderr)
        return 1
    if args.limit:
        proxies = proxies[: args.limit]

    print(f"Probing proxy-sponsor inventory via {len(proxies)} proxy/proxies\n")
    all_found: list[str] = []
    for proxy in proxies:
        all_found.extend(await _probe(cfg, proxy))
        await asyncio.sleep(2.0)

    uniq = sorted(set(all_found))
    print(f"\n=== {len(uniq)} promoted peer(s) discovered ===")
    for u in uniq:
        print(f"  @{u}")
    if not uniq:
        print("  (none — this source yields nothing on these proxies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
