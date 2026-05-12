"""Tests for privacy-safe logging processors."""

from __future__ import annotations

from tg_voice_transcriber.logging import _hash_id, _privacy_processor


class TestHashId:
    def test_returns_12_char_hex(self):
        result = _hash_id(12345)
        assert len(result) == 12
        assert all(c in "0123456789abcdef" for c in result)

    def test_same_input_same_output(self):
        assert _hash_id(99999) == _hash_id(99999)

    def test_different_input_different_output(self):
        assert _hash_id(11111) != _hash_id(22222)


class TestPrivacyProcessor:
    def test_hashes_chat_id(self):
        event = {"chat_id": 12345, "event": "test"}
        result = _privacy_processor(None, "info", event)
        assert result["chat_id"] != 12345
        assert len(result["chat_id"]) == 12

    def test_hashes_sender_id(self):
        event = {"sender_id": 67890, "event": "test"}
        result = _privacy_processor(None, "info", event)
        assert result["sender_id"] != 67890

    def test_strips_transcript_at_info(self):
        event = {"transcript": "secret text", "event": "test"}
        result = _privacy_processor(None, "info", event)
        assert "transcript" not in result

    def test_strips_transcript_at_warning(self):
        event = {"transcript": "secret text", "event": "test"}
        result = _privacy_processor(None, "warning", event)
        assert "transcript" not in result

    def test_none_chat_id_hashed(self):
        event = {"chat_id": None, "event": "test"}
        result = _privacy_processor(None, "info", event)
        # None should not be hashed
        assert result["chat_id"] is None
