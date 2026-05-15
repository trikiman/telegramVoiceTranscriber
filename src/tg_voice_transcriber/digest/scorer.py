"""LLM batch scorer: scores buffered channel posts for relevance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog

from tg_voice_transcriber.groq_client import GroqClient
from tg_voice_transcriber.llm_failover import ChatCompletionClient

log = structlog.get_logger()

DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Max posts per batch to avoid overly long prompts that may hit context limits
MAX_POSTS_PER_BATCH = 100


@dataclass(frozen=True)
class ScoredPost:
    """A post with its LLM score and summary."""

    post_id: int  # post_buffer.id
    channel_id: int
    message_id: int
    channel_title: str
    channel_username: str | None
    score: int  # 1-10
    summary: str
    original_text: str
    # Optional deal detection (v1.1)
    is_deal: bool = False  # LLM detected a free/discounted item
    deal_value_usd: int | None = None  # estimated value if it's a deal
    scam_suspected: bool = False  # LLM flagged suspicious patterns


def _build_system_prompt(user_prefs: str) -> str:
    """Compose the system prompt for relevance scoring."""
    prefs = user_prefs.strip() or "I'm interested in tech, software, and news; not interested in crypto pumps, giveaways, or spam."
    return (
        "You are a relevance filter for a personal Telegram digest. "
        "Score each post 1-10 where 10 = must-read, 7+ = relevant, 5-6 = borderline, 1-4 = skip. "
        "Write summaries in the user's preferred language (Russian or English, match the post language). "
        "\n\n"
        "SUMMARY RULES — the summary must be SELF-CONTAINED. User should NOT need to click the link to understand "
        "what to do. For each post include the essential info:\n"
        "- WHAT it is (the offer/news/announcement)\n"
        "- KEY DETAILS (price, value, deadline, platform)\n"
        "- HOW TO ACT if action is needed (e.g. 'claim at store.epicgames.com' or 'use code FREE2026 in Steam')\n"
        "Write 1-2 concise sentences, not one-liners. Do NOT say 'see post for details' — extract the details.\n"
        "\n\n"
        "ALSO detect free deals, giveaways, and promos. If a post offers something FREE or heavily discounted "
        "(software, games, courses, ebooks, subscriptions, etc.), set `is_deal=true` and estimate its normal value in USD "
        "in `deal_value_usd` (integer). If the post has scam patterns (requires payment to 'claim', asks for card details, "
        "promises unrealistic rewards, shady links, or is clearly a lead-magnet disguised as 'free'), set `scam_suspected=true`. "
        "Boost the score to 8-10 for LEGITIMATE free deals worth $20+ that the user would plausibly want, "
        "regardless of other interests. DO NOT boost scams — score those 1-3 even if they claim high value. "
        "\n\n"
        "Output MUST be valid JSON only, no prose.\n\n"
        f"User's interests: {prefs}"
    )


def _build_user_prompt(posts: list[dict[str, Any]]) -> str:
    """Compose the user prompt containing the posts to score."""
    items = []
    for p in posts:
        items.append({
            "id": p["id"],
            "channel": p.get("channel_username") or p.get("channel_title") or str(p["channel_id"]),
            "text": (p["text"] or "")[:1500],  # clamp to avoid runaway context
        })
    return (
        "Score these posts. Return JSON with this exact shape:\n"
        '{"results": [{"id": <int>, "score": <1-10>, "summary": "<one line>", '
        '"is_deal": <bool>, "deal_value_usd": <int or null>, "scam_suspected": <bool>}, ...]}\n\n'
        "Posts:\n" + json.dumps(items, ensure_ascii=False, indent=2)
    )


class DigestScorer:
    """Batches posts and scores them via the shared Groq client."""

    def __init__(self, groq: GroqClient | ChatCompletionClient, model: str = DEFAULT_MODEL) -> None:
        # ``groq`` historical name; accepts any object with chat_completion().
        # In production with v1.2+ this is a FailoverChatClient(primary=Groq, fallback=OpenRouter).
        self._groq = groq
        self._model = model

    async def score_batch(
        self,
        posts: list[dict[str, Any]],
        user_prefs: str,
    ) -> list[ScoredPost]:
        """Score a batch of buffered posts.

        Args:
            posts: rows from digest_db.drain_buffer()
            user_prefs: free-form text describing user interests

        Returns:
            List of ScoredPost (same length as input, preserving order).
            On LLM failure after retry, returns all posts with score=0 (so caller can drop them).
        """
        if not posts:
            return []

        # Chunk if too large
        if len(posts) > MAX_POSTS_PER_BATCH:
            log.warning(
                "digest_batch_chunking",
                total=len(posts),
                chunk_size=MAX_POSTS_PER_BATCH,
            )
            results: list[ScoredPost] = []
            for i in range(0, len(posts), MAX_POSTS_PER_BATCH):
                chunk = posts[i : i + MAX_POSTS_PER_BATCH]
                results.extend(await self.score_batch(chunk, user_prefs))
            return results

        messages = [
            {"role": "system", "content": _build_system_prompt(user_prefs)},
            {"role": "user", "content": _build_user_prompt(posts)},
        ]

        # Attempt once, retry once on parse failure with stricter prompt
        parsed = None
        for attempt in (1, 2):
            try:
                response = await self._groq.chat_completion(
                    messages=messages,
                    model=self._model,
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
            except Exception as exc:
                log.error(
                    "digest_llm_request_failed",
                    attempt=attempt,
                    error=str(exc),
                )
                continue

            content = self._extract_content(response)
            parsed = self._parse_json(content)
            if parsed is not None:
                break

            log.warning(
                "digest_llm_bad_json",
                attempt=attempt,
                content_preview=content[:200] if content else None,
            )
            # Retry with extra-strict instruction
            messages.append({
                "role": "system",
                "content": "Your previous response was not valid JSON. Return ONLY JSON matching the schema. No prose.",
            })

        if parsed is None:
            log.error("digest_llm_unrecoverable", batch_size=len(posts))
            # Return zero-scored versions so the caller can drop the batch
            return [self._make_failed(p) for p in posts]

        return self._build_scored(posts, parsed)

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
            results = parsed.get("results")
            if not isinstance(results, list):
                return None
            return parsed
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _build_scored(
        posts: list[dict[str, Any]],
        parsed: dict,
    ) -> list[ScoredPost]:
        """Merge LLM scores back into ScoredPost objects."""
        by_id: dict[int, dict] = {}
        for r in parsed.get("results", []):
            if isinstance(r, dict) and "id" in r:
                try:
                    by_id[int(r["id"])] = r
                except (TypeError, ValueError):
                    continue

        scored = []
        for p in posts:
            r = by_id.get(p["id"])
            if r is None:
                # LLM skipped this one — treat as low score
                score = 0
                summary = ""
            else:
                try:
                    score = max(1, min(10, int(r.get("score", 0))))
                except (TypeError, ValueError):
                    score = 0
                summary = str(r.get("summary", "")).strip()[:500]

            scored.append(ScoredPost(
                post_id=p["id"],
                channel_id=p["channel_id"],
                message_id=p["message_id"],
                channel_title=p.get("channel_title") or str(p["channel_id"]),
                channel_username=p.get("channel_username"),
                score=score,
                summary=summary,
                original_text=p.get("text") or "",
                is_deal=bool(r.get("is_deal", False)) if r else False,
                deal_value_usd=_parse_int_or_none(r.get("deal_value_usd")) if r else None,
                scam_suspected=bool(r.get("scam_suspected", False)) if r else False,
            ))

        # Log per-post scores for debugging
        import structlog
        slog = structlog.get_logger()
        for sp in scored:
            slog.info(
                "digest_post_scored",
                channel=sp.channel_username or sp.channel_title[:20],
                score=sp.score,
                summary=sp.summary[:80],
                is_deal=sp.is_deal,
            )

        return scored

    @staticmethod
    def _make_failed(post: dict) -> ScoredPost:
        return ScoredPost(
            post_id=post["id"],
            channel_id=post["channel_id"],
            message_id=post["message_id"],
            channel_title=post.get("channel_title") or str(post["channel_id"]),
            channel_username=post.get("channel_username"),
            score=0,
            summary="",
            original_text=post.get("text") or "",
        )


def _parse_int_or_none(value: Any) -> int | None:
    """Parse an int, returning None for null/invalid inputs."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
