"""LLM-powered VPN trial offer judge.

Evaluates channel posts and sponsored ads to find good VPN/proxy trials:
- Long duration for free/cheap (30 days free, 7+ days for 0-1₽, etc.)
- Scam detection (fake offers, lead magnets, card-harvesting)
- Bot extraction from text, inline buttons, or ad sponsor links
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from tg_voice_transcriber.finder.links import LinkTarget
from tg_voice_transcriber.llm_failover import ChatCompletionClient

log = structlog.get_logger()

DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Cheap deterministic pre-filter. The LLM system prompt is ~450 tokens sent on
# EVERY call, and the Groq free tier caps at 6000 TPM — so ~10 calls exhausts a
# minute. Most sponsored ads are plainly not VPN trials (games, shops, IT
# communities, cars). Reject those with zero LLM cost, and only spend a call on
# ads that at least mention VPN/proxy AND some free/cheap-trial signal.

# Substrings that mark the ad as VPN/proxy-related (case-insensitive).
_VPN_MARKERS = (
    "vpn", "впн", "прокси", "proxy", "mtproto", "обход блокир",
    "обход глушилк", "доступ к сайт", "без блокир",
)

# Substrings that mark a possible free/cheap trial (case-insensitive).
_TRIAL_MARKERS = (
    "бесплат", "free", "trial", "пробн", "триал",
    "0₽", "0 ₽", "1₽", "1 ₽", "за 1", "за 0", "0 руб", "1 руб",
    "дней", "дня", "день", "недел", "месяц", "day", "week", "month",
)


def _passes_prefilter(text: str) -> bool:
    """True if the ad is worth spending an LLM call on.

    Requires BOTH a VPN/proxy marker and a trial/duration/price marker. This
    kills the large majority of ads (games, shops, communities) for free and
    keeps the LLM budget for genuine candidates. Ers toward keeping: if either
    signal is present via a broad marker, we let the LLM make the real call.
    """
    low = text.lower()
    has_vpn = any(m in low for m in _VPN_MARKERS)
    if not has_vpn:
        return False
    has_trial = any(m in low for m in _TRIAL_MARKERS)
    return has_trial



@dataclass(frozen=True)
class JudgedOffer:
    """A VPN/proxy offer that passed the judge."""

    # Source
    source_channel_id: int
    source_message_id: int | None  # None for sponsored ads
    source_text: str

    # Judgment
    is_good_trial: bool  # True if it's a legit long/cheap trial worth collecting
    trial_days: int | None  # e.g. 30, 7, 3 — duration of the trial
    trial_price_rub: float | None  # e.g. 0.0, 1.0 — price in rubles (0 = free)
    scam_suspected: bool  # True if the offer looks fake/scammy

    # Target bot/channel to collect
    target_bot: str | None  # @username of the bot to /start, or None if not extractable
    start_param: str | None  # deep-link ?start= token — send `/start <token>` to claim
    summary: str  # One-line human summary of the offer


def _build_system_prompt() -> str:
    """System prompt for the VPN trial judge."""
    return (
        "You are a VPN/proxy trial offer evaluator for a Russian Telegram user. "
        "Your ONLY job: identify GOOD *VPN or proxy* trials worth collecting. "
        "\n\n"
        "The service MUST be a VPN or proxy service. If the ad is for anything "
        "else (a car, a phone, an IT community, geo-analytics, a bank, a store, "
        "a cashback app, etc.), set is_good_trial=false — even if it says "
        "'30 days free'. A free trial of a non-VPN product is NOT what we want.\n"
        "\n"
        "GOOD VPN/proxy trials (is_good_trial=true). Apply this simple rule:\n"
        "- FREE (0₽) for 7 or more days → GOOD. "
        "('неделя бесплатно' = 7 days = GOOD. '10 дней бесплатно' = GOOD. "
        "'месяц бесплатно' / '30 дней бесплатно' = GOOD and is the JACKPOT.)\n"
        "- Costs 0₽ or 1₽ for ANY duration → GOOD (0-1₽ is effectively free).\n"
        "- '3 дня бесплатно' or '5 дней бесплатно' (free but UNDER 7 days) → is_good_trial=false.\n"
        "\n"
        "REJECT (is_good_trial=false):\n"
        "- Non-VPN products (see above) — the most common rejection\n"
        "- Free VPN trial SHORTER than 7 days (unless price is 0-1₽, which is the same as free)\n"
        "- VPN costing MORE than 1₽ for any period under 30 days\n"
        "- Ads with NO stated free/cheap trial (just 'fast VPN', 'try our bot') → false\n"
        "- Scams: card details required for a 'free' trial, unrealistic promises, "
        "broken Russian/English mix (scam marker)\n"
        "- Raw proxy connection strings (tg://proxy) with no trial offer\n"
        "\n"
        "target_bot: leave as null. The system extracts the bot from the ad link "
        "separately — do NOT guess it. Only fill it if a @username is explicitly in the text.\n"
        "\n"
        "SUMMARY: one-line Russian summary: service name, duration, price. "
        "E.g. 'NoName VPN — 30 дней бесплатно' or 'FadeVPN — 7 дней за 1₽'. "
        "If rejecting, say why briefly.\n"
        "\n"
        "Output MUST be valid JSON only, no prose."
    )


def _build_user_prompt(text: str, buttons: list[str] | None = None) -> str:
    """Compose the user prompt with the offer text and optional button labels."""
    parts = [f"Offer text:\n{text[:2000]}"]  # clamp to avoid runaway context

    if buttons:
        parts.append(f"\nInline buttons: {', '.join(buttons[:10])}")

    parts.append(
        '\n\nEvaluate this offer. Return JSON:\n'
        '{\n'
        '  "is_good_trial": <bool>,\n'
        '  "trial_days": <int or null>,\n'
        '  "trial_price_rub": <float or null>,\n'
        '  "scam_suspected": <bool>,\n'
        '  "target_bot": "<@username or null>",\n'
        '  "summary": "<one line in Russian>"\n'
        '}'
    )

    return "\n".join(parts)


class OfferJudge:
    """LLM-powered judge for VPN trial offers."""

    def __init__(
        self,
        llm_client: ChatCompletionClient,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._llm = llm_client
        self._model = model

    async def judge_offer(
        self,
        text: str,
        channel_id: int,
        message_id: int | None = None,
        buttons: list[str] | None = None,
        link_target: LinkTarget | None = None,
    ) -> JudgedOffer | None:
        """Judge a VPN/proxy offer from a post or ad.

        Args:
            text: the offer text (post message or ad message)
            channel_id: source channel id
            message_id: source message id (None for sponsored ads)
            buttons: optional inline button labels (helps extract target bot)
            link_target: deterministically-parsed deep-link target (from an ad
                url or post button). When present and a bot, this AUTHORITATIVELY
                sets target_bot/start_param — the LLM only judges offer quality.

        Returns:
            JudgedOffer if the LLM judged it successfully, None on error.
        """
        # Cheap deterministic pre-filter: skip the LLM entirely for ads that
        # can't be a VPN trial. Preserves the deep-link target for logging.
        if not _passes_prefilter(text):
            target_bot = None
            start_param = None
            if link_target is not None and link_target.is_bot:
                target_bot = link_target.at_username
                start_param = link_target.start_param
            return JudgedOffer(
                source_channel_id=channel_id,
                source_message_id=message_id,
                source_text=text[:500],
                is_good_trial=False,
                trial_days=None,
                trial_price_rub=None,
                scam_suspected=False,
                target_bot=target_bot,
                start_param=start_param,
                summary="(pre-filtered: not a VPN trial)",
            )

        messages = [
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": _build_user_prompt(text, buttons)},
        ]

        try:
            response = await self._llm.chat_completion(
                messages=messages,
                model=self._model,
                response_format={"type": "json_object"},
                temperature=0.2,
            )
        except Exception as exc:
            log.error(
                "judge_llm_failed",
                channel_id=channel_id,
                message_id=message_id,
                error=str(exc),
            )
            return None

        content = self._extract_content(response)
        parsed = self._parse_json(content)

        if parsed is None:
            log.warning(
                "judge_bad_json",
                channel_id=channel_id,
                message_id=message_id,
                content_preview=content[:200] if content else None,
            )
            return None

        # Prefer the deterministic deep-link target over the LLM's guess.
        # A parsed bot link is authoritative; the LLM guess is a fallback.
        llm_bot = _normalize_bot_username(parsed.get("target_bot"))
        if link_target is not None and link_target.is_bot:
            target_bot = link_target.at_username
            start_param = link_target.start_param
        else:
            target_bot = llm_bot
            start_param = None

        return JudgedOffer(
            source_channel_id=channel_id,
            source_message_id=message_id,
            source_text=text[:500],  # truncate for storage
            is_good_trial=bool(parsed.get("is_good_trial", False)),
            trial_days=_parse_int_or_none(parsed.get("trial_days")),
            trial_price_rub=_parse_float_or_none(parsed.get("trial_price_rub")),
            scam_suspected=bool(parsed.get("scam_suspected", False)),
            target_bot=target_bot,
            start_param=start_param,
            summary=str(parsed.get("summary", "")).strip()[:200],
        )

    @staticmethod
    def _extract_content(response: dict) -> str:
        try:
            return response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""

    @staticmethod
    def _parse_json(content: str) -> dict | None:
        if not content:
            return None
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return None
            return parsed
        except json.JSONDecodeError:
            return None


def _parse_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_bot_username(value: Any) -> str | None:
    """Normalize a bot username to @username form, or None."""
    if value is None or value == "null":
        return None

    s = str(value).strip()
    if not s or s.lower() == "null":
        return None

    # Remove leading @ if present, then re-add it
    s = s.lstrip("@")

    # Basic validation: alphanumeric + underscore, ends with "bot"
    if not re.fullmatch(r"[a-zA-Z0-9_]+", s):
        return None

    # Most Telegram bots end with "bot" (but not required — just a heuristic)
    # Return as @username
    return f"@{s}"
