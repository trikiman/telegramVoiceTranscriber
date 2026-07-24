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


def create_chat_client(cfg: Any, *, for_finder: bool = False) -> FailoverChatClient:
    """Build a ready-to-use :class:`FailoverChatClient` from config.

    This centralises the Groq-primary / OpenRouter-fallback construction that
    was previously copy-pasted across ``__main__.py`` and several scripts
    (``dry-run-finder``, ``probe-bots``, ``test-judge`` …). Both underlying
    clients are ``.load()``-ed here, so the returned object is usable
    immediately and closes both providers via :meth:`FailoverChatClient.close`.

    Args:
        cfg: the application ``Config`` (see :mod:`tg_voice_transcriber.config`).
        for_finder: when True, use the finder's fallback model
            (``cfg.finder_fallback_model``); otherwise the digest's
            (``cfg.digest_fallback_model``). The *primary* model is chosen by
            the caller at ``chat_completion`` time, not here.

    Returns:
        A :class:`FailoverChatClient` wrapping Groq (primary) and, when
        OpenRouter keys are configured, OpenRouter (fallback). If only one
        provider is configured, the wrapper degenerates to a pass-through
        around it.

    Raises:
        ValueError: if neither a Groq nor an OpenRouter key is configured.
    """
    # Imported lazily to keep this module import-light (judge.py imports it).
    from tg_voice_transcriber.groq_client import GroqClient
    from tg_voice_transcriber.openrouter_client import OpenRouterClient

    fallback_model = (
        cfg.finder_fallback_model if for_finder else cfg.digest_fallback_model
    )

    groq: GroqClient | None = None
    groq_key = getattr(cfg, "groq_api_key", None)
    if groq_key is not None and groq_key.get_secret_value():
        groq = GroqClient(api_keys=groq_key.get_secret_value())
        groq.load()

    openrouter: OpenRouterClient | None = None
    or_keys = getattr(cfg, "openrouter_api_keys", None)
    if or_keys is not None and or_keys.get_secret_value():
        candidate = OpenRouterClient(api_keys=or_keys.get_secret_value())
        try:
            candidate.load()
            openrouter = candidate
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("openrouter_init_failed_continuing", error=str(exc))
            openrouter = None

    if groq is not None and openrouter is not None:
        log.info(
            "llm_failover_created",
            primary="groq",
            fallback="openrouter",
            fallback_model=fallback_model,
            for_finder=for_finder,
        )
        return FailoverChatClient(
            primary=groq, fallback=openrouter, fallback_model=fallback_model
        )
    if groq is not None:
        log.info("llm_client_created", primary="groq", fallback=None)
        return FailoverChatClient(primary=groq, fallback=None)
    if openrouter is not None:
        log.info("llm_client_created", primary="openrouter", fallback=None)
        return FailoverChatClient(primary=openrouter, fallback=None)

    raise ValueError(
        "No LLM key configured. Set TG_VOICE_GROQ_API_KEY (recommended) "
        "and/or TG_VOICE_OPENROUTER_API_KEYS in your environment/.env."
    )
