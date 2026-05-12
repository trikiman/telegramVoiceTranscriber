"""LLM batch scorer: scores buffered channel posts for relevance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog

from tg_voice_transcriber.groq_client import GroqClient

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


def _build_system_prompt(user_prefs: str) -> str:
    """Compose the system prompt for relevance scoring."""
    prefs = user_prefs.strip() or "I'm interested in tech, software, and news; not interested in crypto pumps, giveaways, or spam."
    return (
        "You are a relevance filter for a personal Telegram digest. "
        "Score each post 1-10 where 10 = must-read, 7+ = relevant, 5-6 = borderline, 1-4 = skip. "
        "Return ONE-line summaries in the user's preferred language (Russian or English, match the post language). "
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
        '{"results": [{"id": <int>, "score": <1-10>, "summary": "<one line>"}, ...]}\n\n'
        "Posts:\n" + json.dumps(items, ensure_ascii=False, indent=2)
    )


class DigestScorer:
    """Batches posts and scores them via the shared Groq client."""

    def __init__(self, groq: GroqClient, model: str = DEFAULT_MODEL) -> None:
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
                summary = str(r.get("summary", "")).strip()[:280]

            scored.append(ScoredPost(
                post_id=p["id"],
                channel_id=p["channel_id"],
                message_id=p["message_id"],
                channel_title=p.get("channel_title") or str(p["channel_id"]),
                channel_username=p.get("channel_username"),
                score=score,
                summary=summary,
                original_text=p.get("text") or "",
            ))
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
