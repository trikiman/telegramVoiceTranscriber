"""OpenRouter API client — fallback LLM provider for digest scoring.

OpenRouter exposes an OpenAI-compatible HTTP API at
``https://openrouter.ai/api/v1``. This client mirrors the interface of
``GroqClient.chat_completion`` so it can be used as a drop-in fallback
when Groq is rate-limited or has all keys exhausted.

OpenRouter recommends sending two optional headers for analytics and
abuse protection:
- ``HTTP-Referer``: identifying URL of the calling app
- ``X-Title``: human-readable name

We send ``HTTP-Referer: https://github.com/local/tg-voice-transcriber``
and ``X-Title: tg-voice-transcriber`` so OpenRouter dashboard groups
our usage cleanly. These are not secrets and fine to be hardcoded.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_REFERER = "https://github.com/local/tg-voice-transcriber"
DEFAULT_TITLE = "tg-voice-transcriber"


@dataclass
class OpenRouterClient:
    """Async OpenRouter client with optional multi-key rotation.

    Usage::

        client = OpenRouterClient(api_keys="sk-or-v1-...")
        client.load()
        reply = await client.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            model="meta-llama/llama-3.3-70b-instruct:free",
        )
        await client.close()

    The interface intentionally matches :class:`GroqClient.chat_completion`
    so :class:`tg_voice_transcriber.llm_failover.FailoverChatClient` can
    swap one for the other without the caller noticing.
    """

    api_keys: str  # comma-separated; supports a single key too
    timeout_s: float = 30.0
    referer: str = DEFAULT_REFERER
    title: str = DEFAULT_TITLE

    _client: object = field(default=None, init=False, repr=False)
    _key_list: list[str] = field(default_factory=list, init=False, repr=False)
    _key_index: int = field(default=0, init=False, repr=False)
    _rotation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def load(self) -> None:
        """Initialize the shared HTTP client. Must be called before use."""
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover — httpx is in install_requires
            raise ImportError(
                "httpx is required. Install: pip install 'httpx>=0.27,<1.0'"
            ) from exc

        self._key_list = [k.strip() for k in self.api_keys.split(",") if k.strip()]
        if not self._key_list:
            raise ValueError("At least one OpenRouter API key is required")

        self._client = httpx.AsyncClient(
            base_url=OPENROUTER_BASE_URL,
            timeout=self.timeout_s,
        )
        log.info("openrouter_client_ready", key_pool_size=len(self._key_list))

    def _current_key(self) -> str:
        return self._key_list[self._key_index % len(self._key_list)]

    def _rotate_key(self) -> None:
        self._key_index = (self._key_index + 1) % len(self._key_list)
        log.info(
            "openrouter_key_rotated",
            index=self._key_index,
            pool_size=len(self._key_list),
        )

    async def _request_with_rotation(
        self,
        path: str,
        *,
        json: dict,
    ) -> dict:
        """POST a JSON request, rotating keys on 429 up to pool size attempts."""
        if self._client is None:
            raise RuntimeError("OpenRouterClient.load() must be called first")

        attempts = len(self._key_list)
        last_error: Exception | None = None

        for _ in range(attempts):
            api_key = self._current_key()
            headers = {
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": self.referer,
                "X-Title": self.title,
                "Content-Type": "application/json",
            }
            try:
                response = await self._client.post(  # type: ignore[attr-defined]
                    path, headers=headers, json=json
                )
            except Exception as exc:
                log.error("openrouter_request_exception", path=path, error=str(exc))
                raise

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429:
                log.warning("openrouter_rate_limited", path=path, will_rotate=True)
                async with self._rotation_lock:
                    self._rotate_key()
                last_error = Exception(
                    f"429 rate limit: {response.text[:200]}"
                )
                continue

            log.error(
                "openrouter_bad_status",
                path=path,
                status=response.status_code,
                body=response.text[:200],
            )
            response.raise_for_status()

        raise last_error or Exception(
            "All OpenRouter API keys exhausted (rate-limited)"
        )

    async def chat_completion(
        self,
        messages: list[dict],
        *,
        model: str = "meta-llama/llama-3.3-70b-instruct:free",
        response_format: dict | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict:
        """POST /chat/completions — returns the raw OpenAI-compatible response dict.

        The response shape (``choices[0].message.content``) matches Groq exactly,
        so :class:`DigestScorer` can consume either provider's output.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        return await self._request_with_rotation("/chat/completions", json=payload)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()  # type: ignore[attr-defined]
            self._client = None
            log.debug("openrouter_client_closed")
