"""Tests for the transcriber module (hallucination detection, language clamping)."""

from __future__ import annotations

import pytest

from tg_voice_transcriber.transcriber import (
    ALLOWED_LANGUAGES,
    Transcriber,
    TranscriptResult,
)


class TestHallucinationDetection:
    """Verify known hallucination phrases are suppressed."""

    @pytest.mark.parametrize(
        "text",
        [
            "Thanks for watching",
            "thanks for watching",
            "Спасибо за просмотр",
            "спасибо за просмотр",
            "♪",
            "  ",
            "",
            "ab",  # too short
            "Подписывайтесь на канал",
        ],
    )
    def test_hallucination_detected(self, text: str):
        assert Transcriber._is_hallucination(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Hello, how are you?",
            "Привет, как дела?",
            "This is a normal sentence about watching TV",
            "I need to subscribe to the newsletter tomorrow",
        ],
    )
    def test_normal_text_not_hallucination(self, text: str):
        assert Transcriber._is_hallucination(text) is False


class TestLanguageClamping:
    """Verify only ru and en are allowed."""

    def test_allowed_languages_set(self):
        assert "ru" in ALLOWED_LANGUAGES
        assert "en" in ALLOWED_LANGUAGES
        assert "zh" not in ALLOWED_LANGUAGES


class TestTranscriberNotLoaded:
    """Verify proper error when model not loaded."""

    @pytest.mark.asyncio
    async def test_transcribe_without_load_raises(self):
        t = Transcriber()
        with pytest.raises(RuntimeError, match="load\\(\\) must be called"):
            await t.transcribe(b"\x00" * 100)
