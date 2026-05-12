"""Tests for the formatter module."""

from __future__ import annotations

from tg_voice_transcriber.formatter import format_error, format_placeholder, format_transcript
from tg_voice_transcriber.transcriber import TranscriptResult


class TestFormatTranscript:
    def test_empty_text_returns_silence(self):
        result = TranscriptResult(text="", language="ru", segments_count=0)
        assert format_transcript(result) == ["(silence)"]

    def test_short_text_single_message(self):
        result = TranscriptResult(text="Hello world", language="en", segments_count=1)
        parts = format_transcript(result)
        assert len(parts) == 1
        assert parts[0] == "Hello world"

    def test_long_text_splits(self):
        # Create text longer than 4096 chars
        long_text = "word " * 1000  # ~5000 chars
        result = TranscriptResult(text=long_text, language="en", segments_count=10)
        parts = format_transcript(result)
        assert len(parts) >= 2
        for part in parts:
            assert len(part) <= 4096

    def test_strips_whitespace(self):
        result = TranscriptResult(text="  hello  ", language="en", segments_count=1)
        parts = format_transcript(result)
        assert parts[0] == "hello"


class TestPlaceholderAndError:
    def test_placeholder(self):
        assert "⏳" in format_placeholder()
        assert "Transcribing" in format_placeholder()

    def test_error(self):
        assert "❌" in format_error()
        assert "failed" in format_error()
