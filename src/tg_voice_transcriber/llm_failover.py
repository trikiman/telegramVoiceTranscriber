"""Failover wrapper for chat-completion-shaped LLM clients.

Wraps a primary client (e.g. :class:`GroqClient`) and an optional fallback
(e.g. :class:`OpenRouterClient`). On any exception from the primary, the
wrapper retries once on the fallback, translating the model name if needed.

This keeps :class:`DigestScorer` provider-agnostic — it sees a single
``chat_completion``-shaped object regardless of how many providers we
chain underneath.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import structlog

log = structlog.get_logger()


class ChatCompletionClient(Protocol):
    """Structural protocol for any client exposing ``chat_completion``."""

    async def chat_completion(
        self,
        messages: list[dict],
        *,
        model: str,
        response_format: dict | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict: ...

    async def close(self) -> None: ...


@dataclass
class FailoverChatClient:
    """Try ``primary`` first; on exception, try ``fallback`` once.

    The fallback's model name is taken from ``fallback_model`` (different
    providers use different model identifiers — Groq calls it
    ``llama-3.3-70b-versatile``, OpenRouter calls it
    ``meta-llama/llama-3.3-70b-instruct:free``).

    If ``fallback`` is ``None`` the wrapper degenerates to a pass-through
    around ``primary``. This lets the caller construct a uniform object
    regardless of whether a fallback is configured.
    """

    primary: ChatCompletionClient
    fallback: ChatCompletionClient | None = None
    fallback_model: str | None = None

    async def chat_completion(
        self,
        messages: list[dict],
        *,
        model: str,
        response_format: dict | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict:
        """Run ``chat_completion`` on primary; on failure, retry on fallback."""
        try:
            return await self.primary.chat_completion(
                messages,
                model=model,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            if self.fallback is None:
                # No fallback configured — bubble up
                raise
            primary_name = type(self.primary).__name__
            fb_name = type(self.fallback).__name__
            fb_model = self.fallback_model or model
            log.warning(
                "llm_primary_failed_using_fallback",
                primary=primary_name,
                fallback=fb_name,
                fallback_model=fb_model,
                error=str(exc)[:200],
            )
            return await self.fallback.chat_completion(
                messages,
                model=fb_model,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    async def close(self) -> None:
        """Close both underlying clients (best-effort)."""
        try:
            await self.primary.close()
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("llm_primary_close_failed", error=str(exc))
        if self.fallback is not None:
            try:
                await self.fallback.close()
            except Exception as exc:  # pragma: no cover
                log.warning("llm_fallback_close_failed", error=str(exc))
