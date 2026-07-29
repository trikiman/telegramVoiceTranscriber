#!/usr/bin/env python3
"""Discover VPN bots from WEB catalogs, then live-verify them.

The other discovery sources are all inside Telegram, so they can only surface
what the finder account can already reach — which is why they kept returning
the same recycled bots. Web bot-catalogs index by keyword and expose bots
Telegram's own search never shows: a single sweep of tgramsearch.com found 23
unseen VPN bots, one of which (@molniya_vpn_bot, 10 дней) was the first
genuine qualifying find in days.

This script only DISCOVERS usernames — every candidate still goes through the
same live `/start` verification as any other source, because catalog blurbs
lie exactly like ads do.

Requires no browser: the catalogs render usernames in server-side HTML.

Usage:
    python scripts/discover-web-bots.py                    # sweep default queries
    python scripts/discover-web-bots.py --query "vpn trial"
    python scripts/discover-web-bots.py --out .local/web-bots.txt
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

OUT_DEFAULT = Path(__file__).resolve().parent.parent / ".local" / "web-bots.txt"

# Catalogs that render bot usernames in plain server-side HTML. tgramsearch is
# the productive one; tgramhub renders via JS (nothing to scrape) and
# telegramchannels.me yields very little, so they are not included.
SEARCH_URL = "https://tgramsearch.com/search?query={}"

DEFAULT_QUERIES: tuple[str, ...] = (
    "vpn",
    "бесплатный vpn",
    "vpn бесплатно",
    "впн",
    "free vpn",
    "vpn пробный",
    "vless",
)

# @username where the name ends in bot/robot — same convention as the rest of
# the pipeline (finder/harvest.py::BOT_LINK_REGEX).
_BOT_RE = re.compile(r"@([A-Za-z0-9_]{3,40}(?:bot|robot))\b", re.IGNORECASE)

# Support/feedback/news bots are not VPN services — skip them so the /start
# budget is not spent on obvious non-candidates.
_SKIP_MARKERS = ("support", "help", "feedback", "news", "privet", "privatka", "sub_bot")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _fetch(url: str, timeout: float = 25.0, attempts: int = 3) -> str:
    """Fetch with retries — the catalog rate-limits a fast query sweep.

    Sweeping all queries back-to-back makes tgramsearch drop the SSL handshake
    partway through, and the first version of this script treated that as "no
    bots found": a 7-query sweep reported 0 candidates while a single query
    returned 22. Silent fetch failure looks identical to an empty catalog, so
    retry with backoff and let the caller distinguish the two.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(2.0 * attempt)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    raise last_exc if last_exc else RuntimeError("fetch failed")


def _looks_like_service_bot(username: str) -> bool:
    low = username.lower()
    return not any(m in low for m in _SKIP_MARKERS)


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover VPN bots from web catalogs")
    ap.add_argument("--query", action="append", default=None,
                    help="Search term (repeatable). Defaults to a VPN sweep.")
    ap.add_argument("--out", default=str(OUT_DEFAULT),
                    help="Write discovered usernames here (one per line).")
    args = ap.parse_args()

    queries = tuple(args.query) if args.query else DEFAULT_QUERIES

    found: dict[str, str] = {}   # lowercased -> original casing
    skipped: set[str] = set()

    failed: list[str] = []
    for i, query in enumerate(queries):
        if i:
            # Pacing, not politeness theatre: back-to-back queries get the
            # handshake dropped, which costs more time than waiting.
            time.sleep(random.uniform(3.0, 5.0))
        url = SEARCH_URL.format(urllib.parse.quote_plus(query))
        try:
            html = _fetch(url)
        except Exception as exc:  # noqa: BLE001
            failed.append(query)
            print(f"  ! {query!r}: fetch failed ({type(exc).__name__})", file=sys.stderr)
            continue

        hits = 0
        for match in _BOT_RE.finditer(html):
            uname = match.group(1)
            key = uname.lower()
            if key in found or key in skipped:
                continue
            if _looks_like_service_bot(uname):
                found[key] = uname
                hits += 1
            else:
                skipped.add(key)
        print(f"  {query!r}: +{hits} new")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    names = sorted(found.values(), key=str.lower)
    out_path.write_text("\n".join(names) + "\n", encoding="utf-8")

    if failed:
        # Loud on purpose: an all-failed sweep prints "0 candidates", which
        # reads exactly like a genuinely empty catalog unless we say otherwise.
        print(f"\n⚠ {len(failed)}/{len(queries)} quer(ies) failed to fetch: "
              f"{', '.join(repr(q) for q in failed)}")
        if not names:
            print("  0 candidates below is a FETCH FAILURE, not an empty catalog.")

    print(f"\n=== {len(names)} candidate bot(s) -> {out_path} ===")
    for n in names:
        print(f"  @{n}")
    print(f"\n({len(skipped)} support/news bots skipped)")
    print("\nNext — live-verify them (nothing is trusted until /start-ed):")
    print(f"  python scripts/probe-bots.py $(cat {out_path} | tr '\\n' ' ')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
