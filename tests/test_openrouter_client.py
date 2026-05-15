"""Tests for OpenRouterClient — multi-key rotation, headers, error handling."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tg_voice_transcriber.openrouter_client import (
    DEFAULT_REFERER,
    DEFAULT_TITLE,
    OPENROUTER_BASE_URL,
    OpenRouterClient,
)


class _FakeResponse:
    def __init__(self, status_code: int, body: dict | str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self) -> dict:
        if isinstance(self._body, dict):
            return self._body
        return json.loads(self._body)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeAsyncClient:
    """Minimal stand-in for ``httpx.AsyncClient`` for unit tests."""

    def __init__(self, base_url: str = "", timeout: float = 30.0) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.posts: list[dict[str, Any]] = []
        self._responses: list[_FakeResponse] = []
        self.closed = False

    def queue(self, *responses: _FakeResponse) -> None:
        self._responses.extend(responses)

    async def post(self, path: str, *, headers: dict, json: dict | None = None, **kw: Any) -> _FakeResponse:  # noqa: A002
        self.posts.append({"path": path, "headers": headers, "json": json, "extra": kw})
        if not self._responses:
            raise AssertionError(f"unexpected POST {path}")
        return self._responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def _make_client_with_fake(api_keys: str = "sk-or-test-1") -> tuple[OpenRouterClient, _FakeAsyncClient]:
    """Build an OpenRouterClient with the http client swapped for a fake."""
    fake = _FakeAsyncClient(base_url=OPENROUTER_BASE_URL)
    client = OpenRouterClient(api_keys=api_keys)
    # Bypass load() since httpx isn't actually called
    client._key_list = [k.strip() for k in api_keys.split(",") if k.strip()]
    client._key_index = 0
    client._client = fake
    return client, fake


@pytest.mark.asyncio
async def test_chat_completion_success_returns_dict() -> None:
    client, fake = _make_client_with_fake()
    fake.queue(_FakeResponse(200, {"choices": [{"message": {"content": "hi"}}]}))
    out = await client.chat_completion(
        messages=[{"role": "user", "content": "hello"}],
        model="meta-llama/llama-3.3-70b-instruct:free",
    )
    assert out["choices"][0]["message"]["content"] == "hi"
    assert len(fake.posts) == 1
    p = fake.posts[0]
    assert p["path"] == "/chat/completions"
    assert p["json"]["model"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert p["json"]["temperature"] == 0.2
    # Headers include OpenRouter analytics + auth
    assert p["headers"]["Authorization"] == "Bearer sk-or-test-1"
    assert p["headers"]["HTTP-Referer"] == DEFAULT_REFERER
    assert p["headers"]["X-Title"] == DEFAULT_TITLE


@pytest.mark.asyncio
async def test_response_format_and_max_tokens_passthrough() -> None:
    client, fake = _make_client_with_fake()
    fake.queue(_FakeResponse(200, {"choices": [{"message": {"content": "{}"}}]}))
    await client.chat_completion(
        messages=[{"role": "user", "content": "x"}],
        model="m",
        response_format={"type": "json_object"},
        max_tokens=128,
        temperature=0.7,
    )
    body = fake.posts[0]["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 128
    assert body["temperature"] == 0.7


@pytest.mark.asyncio
async def test_429_rotates_to_next_key_then_succeeds() -> None:
    client, fake = _make_client_with_fake(api_keys="key-a, key-b, key-c")
    # First request 429 → rotate; second request 200
    fake.queue(
        _FakeResponse(429, "rate"),
        _FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]}),
    )
    out = await client.chat_completion(messages=[{"role": "user", "content": "x"}], model="m")
    assert out["choices"][0]["message"]["content"] == "ok"
    # Two attempts, two different keys
    assert len(fake.posts) == 2
    assert fake.posts[0]["headers"]["Authorization"] == "Bearer key-a"
    assert fake.posts[1]["headers"]["Authorization"] == "Bearer key-b"


@pytest.mark.asyncio
async def test_429_on_all_keys_raises() -> None:
    client, fake = _make_client_with_fake(api_keys="k1,k2")
    fake.queue(_FakeResponse(429, "rate1"), _FakeResponse(429, "rate2"))
    with pytest.raises(Exception, match="429"):
        await client.chat_completion(messages=[{"role": "user", "content": "x"}], model="m")
    assert len(fake.posts) == 2  # both keys tried


@pytest.mark.asyncio
async def test_non_429_error_does_not_rotate() -> None:
    client, fake = _make_client_with_fake(api_keys="k1,k2")
    # 401 should raise immediately, not retry on next key
    fake.queue(_FakeResponse(401, "unauthorized"))
    with pytest.raises(RuntimeError, match="http 401"):
        await client.chat_completion(messages=[{"role": "user", "content": "x"}], model="m")
    assert len(fake.posts) == 1


@pytest.mark.asyncio
async def test_close_closes_underlying_client() -> None:
    client, fake = _make_client_with_fake()
    await client.close()
    assert fake.closed is True
    assert client._client is None


def test_load_requires_at_least_one_key() -> None:
    client = OpenRouterClient(api_keys="")
    with pytest.raises(ValueError, match="(?i)at least one"):
        client.load()


def test_load_requires_at_least_one_key_after_strip() -> None:
    client = OpenRouterClient(api_keys="  , ,  ")
    with pytest.raises(ValueError, match="(?i)at least one"):
        client.load()


@pytest.mark.asyncio
async def test_request_without_load_raises() -> None:
    client = OpenRouterClient(api_keys="k")
    # No _client set
    with pytest.raises(RuntimeError, match="load"):
        await client.chat_completion(messages=[], model="m")
