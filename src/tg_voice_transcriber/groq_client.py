"""Shared Groq API client with multi-key rotation.

Supports both whisper (audio transcription) and chat completions (LLM).
All requests rotate through a pool of API keys on HTTP 429 responses.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger()

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass
class GroqClient:
    """Shared async Groq client with round-robin API key rotation.

    Usage::

        client = GroqClient(api_keys="gsk_a,gsk_b,gsk_c")
        client.load()
        transcript = await client.transcribe_ogg(ogg_bytes, language="ru")
        reply = await client.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            model="llama-3.3-70b-versatile",
        )
        await client.close()
    """

    api_keys: str  # comma-separated
    timeout_s: float = 30.0

    _client: object = field(default=None, init=False, repr=False)
    _key_list: list[str] = field(default_factory=list, init=False, repr=False)
    _key_index: int = field(default=0, init=False, repr=False)
    _rotation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def load(self) -> None:
        """Initialize the shared HTTP client."""
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "httpx is required. Install: pip install 'httpx>=0.27,<1.0'"
            ) from exc

        self._key_list = [k.strip() for k in self.api_keys.split(",") if k.strip()]
        if not self._key_list:
            raise ValueError("At least one Groq API key is required")

        self._client = httpx.AsyncClient(
            base_url=GROQ_BASE_URL,
            timeout=self.timeout_s,
        )
        log.info("groq_client_ready", key_pool_size=len(self._key_list))

    def _current_key(self) -> str:
        return self._key_list[self._key_index % len(self._key_list)]

    def _rotate_key(self) -> None:
        self._key_index = (self._key_index + 1) % len(self._key_list)
        log.info("groq_key_rotated", index=self._key_index, pool_size=len(self._key_list))

    async def _request_with_rotation(
        self,
        path: str,
        *,
        files: dict | None = None,
        data: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        """Execute a POST request, rotating keys on 429 up to pool size attempts."""
        if self._client is None:
            raise RuntimeError("GroqClient.load() must be called first")

        attempts = len(self._key_list)
        last_error: Exception | None = None

        for _ in range(attempts):
            api_key = self._current_key()
            headers = {"Authorization": f"Bearer {api_key}"}

            try:
                if json is not None:
                    response = await self._client.post(
                        path, headers=headers, json=json
                    )
                else:
                    response = await self._client.post(
                        path, headers=headers, files=files, data=data
                    )
            except Exception as exc:
                log.error("groq_request_exception", path=path, error=str(exc))
                raise

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429:
                log.warning("groq_rate_limited", path=path, will_rotate=True)
                async with self._rotation_lock:
                    self._rotate_key()
                last_error = Exception(f"429 rate limit: {response.text[:200]}")
                continue

            log.error(
                "groq_bad_status",
                path=path,
                status=response.status_code,
                body=response.text[:200],
            )
            response.raise_for_status()

        raise last_error or Exception("All Groq API keys exhausted (rate-limited)")

    async def transcribe_ogg(
        self,
        ogg_bytes: bytes,
        *,
        language: str | None = None,
        model: str = "whisper-large-v3-turbo",
    ) -> dict:
        """POST to /audio/transcriptions — returns raw Groq response dict."""
        files = {"file": ("voice.ogg", ogg_bytes, "audio/ogg")}
        data: dict[str, str] = {
            "model": model,
            "response_format": "verbose_json",
            "temperature": "0",
        }
        if language:
            data["language"] = language

        return await self._request_with_rotation(
            "/audio/transcriptions", files=files, data=data
        )

    async def chat_completion(
        self,
        messages: list[dict],
        *,
        model: str = "llama-3.3-70b-versatile",
        response_format: dict | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict:
        """POST to /chat/completions — returns raw Groq response dict."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        return await self._request_with_rotation(
            "/chat/completions", json=payload
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            log.debug("groq_client_closed")
