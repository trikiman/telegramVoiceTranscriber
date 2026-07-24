"""Parse Telegram t.me / tg:// links into (username, start_param) targets.

Sponsored ads and post buttons point to bots via deep links like
``https://t.me/vpn_portbot?start=OFFER_TOKEN``. The bot username and the
``start`` token are both deterministically extractable here — no LLM needed.
Sending ``/start OFFER_TOKEN`` (rather than a bare ``/start``) is what actually
claims the advertised trial, so we preserve the token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

# Usernames that are t.me paths but never a resolvable bot/channel target.
_RESERVED_PATHS = {
    "joinchat", "addstickers", "addemoji", "share", "proxy", "socks",
    "login", "confirmphone", "setlanguage", "bg", "invoice", "iv",
}


@dataclass(frozen=True)
class LinkTarget:
    """A resolved deep-link target."""

    username: str  # without leading @
    start_param: str | None  # the ?start= / ?startapp= token, if any
    is_bot: bool  # True if we're confident the target is a bot

    @property
    def at_username(self) -> str:
        return f"@{self.username}"


def parse_tme_target(
    url: str | None,
    *,
    button_text: str | None = None,
) -> LinkTarget | None:
    """Parse a t.me / tg:// URL into a LinkTarget, or None if not a user target.

    Args:
        url: the link, e.g. ``https://t.me/vpn_portbot?start=TOKEN``
        button_text: the ad/button label, e.g. "VIEW BOT" / "VIEW CHANNEL" —
            used as a hint for is_bot.

    Returns:
        LinkTarget, or None for private-invite / proxy / non-username links.
    """
    if not url:
        return None

    url = url.strip()
    username: str | None = None
    start_param: str | None = None

    # tg://resolve?domain=foo&start=bar
    if url.startswith("tg://"):
        parts = urlsplit(url)
        q = parse_qs(parts.query)
        domain = q.get("domain", [None])[0]
        if not domain:
            return None
        username = domain
        start_param = _first(q, "start", "startapp", "startgroup")
    else:
        parts = urlsplit(url if "://" in url else f"https://{url}")
        host = parts.netloc.lower()
        if host not in ("t.me", "telegram.me", "telegram.dog"):
            return None
        path = parts.path.lstrip("/")
        if not path:
            return None
        # Private invite (t.me/+HASH or t.me/joinchat/HASH) — no username target
        if path.startswith("+"):
            return None
        first_seg = path.split("/", 1)[0]
        if first_seg.lower() in _RESERVED_PATHS:
            return None
        username = first_seg
        q = parse_qs(parts.query)
        start_param = _first(q, "start", "startapp", "startgroup")

    if username is None:
        return None
    username = username.lstrip("@")
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{3,}", username):
        return None

    is_bot = _looks_like_bot(username, button_text, start_param)
    return LinkTarget(
        username=username,
        start_param=unquote(start_param) if start_param else None,
        is_bot=is_bot,
    )


def _looks_like_bot(
    username: str,
    button_text: str | None,
    start_param: str | None,
) -> bool:
    """Heuristic: is this deep-link target a bot?"""
    if button_text and "bot" in button_text.lower():
        return True
    if button_text and "channel" in button_text.lower():
        return False
    if username.lower().endswith("bot"):
        return True
    # A ?start= deep link is only meaningful for bots.
    return bool(start_param)


def _first(q: dict[str, list[str]], *keys: str) -> str | None:
    for k in keys:
        if k in q and q[k]:
            return q[k][0]
    return None
