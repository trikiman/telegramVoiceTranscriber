"""Tests for FailoverChatClient — primary→fallback semantics."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tg_voice_transcriber.groq_client import GroqClient
from tg_voice_transcriber.llm_failover import FailoverChatClient, create_chat_client
from tg_voice_transcriber.openrouter_client import OpenRouterClient


class _Secret:
    """Minimal stand-in for pydantic SecretStr."""

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def _cfg(*, groq: str | None = None, openrouter: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        groq_api_key=_Secret(groq) if groq is not None else None,
        openrouter_api_keys=_Secret(openrouter) if openrouter is not None else None,
        finder_fallback_model="fb-finder",
        digest_fallback_model="fb-digest",
    )


class _FakeChatClient:
    """Tracks calls and returns canned responses (or raises)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []
        self.next_response: dict | None = None
        self.next_exception: Exception | None = None
        self.closed = False

    def respond_with(self, response: dict) -> None:
        self.next_response = response
        self.next_exception = None

    def fail_with(self, exc: Exception) -> None:
        self.next_exception = exc
        self.next_response = None

    async def chat_completion(
        self,
        messages: list[dict],
        *,
        model: str,
        response_format: dict | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict:
        self.calls.append({
            "messages": messages,
            "model": model,
            "response_format": response_format,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        if self.next_exception is not None:
            raise self.next_exception
        if self.next_response is not None:
            return self.next_response
        raise AssertionError(f"{self.name}.chat_completion called without canned response")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback() -> None:
    primary = _FakeChatClient("primary")
    fallback = _FakeChatClient("fallback")
    primary.respond_with({"choices": [{"message": {"content": "ok"}}]})

    fc = FailoverChatClient(primary=primary, fallback=fallback)
    out = await fc.chat_completion(
        messages=[{"role": "user", "content": "x"}],
        model="primary-model",
    )
    assert out["choices"][0]["message"]["content"] == "ok"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 0


@pytest.mark.asyncio
async def test_primary_failure_invokes_fallback_with_translated_model() -> None:
    primary = _FakeChatClient("primary")
    fallback = _FakeChatClient("fallback")
    primary.fail_with(RuntimeError("groq down"))
    fallback.respond_with({"choices": [{"message": {"content": "from fallback"}}]})

    fc = FailoverChatClient(
        primary=primary,
        fallback=fallback,
        fallback_model="meta-llama/llama-3.3-70b-instruct:free",
    )
    out = await fc.chat_completion(
        messages=[{"role": "user", "content": "x"}],
        model="llama-3.3-70b-versatile",  # primary-style name
        response_format={"type": "json_object"},
        temperature=0.5,
    )
    assert out["choices"][0]["message"]["content"] == "from fallback"
    # Primary called with primary model
    assert primary.calls[0]["model"] == "llama-3.3-70b-versatile"
    # Fallback called with translated model + same kwargs
    assert fallback.calls[0]["model"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert fallback.calls[0]["response_format"] == {"type": "json_object"}
    assert fallback.calls[0]["temperature"] == 0.5


@pytest.mark.asyncio
async def test_fallback_uses_caller_model_when_no_translation_set() -> None:
    primary = _FakeChatClient("primary")
    fallback = _FakeChatClient("fallback")
    primary.fail_with(Exception("boom"))
    fallback.respond_with({"choices": [{"message": {"content": "fb"}}]})

    fc = FailoverChatClient(primary=primary, fallback=fallback, fallback_model=None)
    await fc.chat_completion(messages=[], model="some-model")
    assert fallback.calls[0]["model"] == "some-model"


@pytest.mark.asyncio
async def test_no_fallback_propagates_primary_exception() -> None:
    primary = _FakeChatClient("primary")
    primary.fail_with(RuntimeError("groq error"))

    fc = FailoverChatClient(primary=primary, fallback=None)
    with pytest.raises(RuntimeError, match="groq error"):
        await fc.chat_completion(messages=[], model="m")


@pytest.mark.asyncio
async def test_fallback_failure_also_propagates() -> None:
    primary = _FakeChatClient("primary")
    fallback = _FakeChatClient("fallback")
    primary.fail_with(RuntimeError("primary-fail"))
    fallback.fail_with(RuntimeError("fallback-fail"))

    fc = FailoverChatClient(primary=primary, fallback=fallback)
    with pytest.raises(RuntimeError, match="fallback-fail"):
        await fc.chat_completion(messages=[], model="m")
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_close_closes_both() -> None:
    primary = _FakeChatClient("primary")
    fallback = _FakeChatClient("fallback")
    fc = FailoverChatClient(primary=primary, fallback=fallback)
    await fc.close()
    assert primary.closed
    assert fallback.closed


@pytest.mark.asyncio
async def test_close_with_no_fallback() -> None:
    primary = _FakeChatClient("primary")
    fc = FailoverChatClient(primary=primary, fallback=None)
    await fc.close()
    assert primary.closed


# --- create_chat_client factory (guards the harvester's import-time crash) --

def test_create_chat_client_requires_a_key() -> None:
    with pytest.raises(ValueError, match="No LLM key"):
        create_chat_client(_cfg())


@pytest.mark.asyncio
async def test_create_chat_client_groq_only() -> None:
    fc = create_chat_client(_cfg(groq="gsk_test"))
    assert isinstance(fc, FailoverChatClient)
    assert isinstance(fc.primary, GroqClient)
    assert fc.fallback is None
    await fc.close()


@pytest.mark.asyncio
async def test_create_chat_client_openrouter_only() -> None:
    fc = create_chat_client(_cfg(openrouter="sk-or-test"))
    assert isinstance(fc.primary, OpenRouterClient)
    assert fc.fallback is None
    await fc.close()


@pytest.mark.asyncio
async def test_create_chat_client_failover_uses_finder_fallback_model() -> None:
    fc = create_chat_client(_cfg(groq="gsk_test", openrouter="sk-or-test"), for_finder=True)
    assert isinstance(fc.primary, GroqClient)
    assert isinstance(fc.fallback, OpenRouterClient)
    assert fc.fallback_model == "fb-finder"
    await fc.close()


@pytest.mark.asyncio
async def test_create_chat_client_digest_fallback_model() -> None:
    fc = create_chat_client(_cfg(groq="gsk_test", openrouter="sk-or-test"), for_finder=False)
    assert fc.fallback_model == "fb-digest"
    await fc.close()
