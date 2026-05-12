"""Tests for the audio pipeline (duration guards and conversion logic)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tg_voice_transcriber.audio import (
    AudioConversionError,
    AudioTooLongError,
    AudioTooShortError,
    FFmpegNotFoundError,
    check_ffmpeg,
    convert_voice_note,
)


class TestDurationGuards:
    """Duration checks happen before FFmpeg is invoked."""

    def test_too_short_raises(self):
        with pytest.raises(AudioTooShortError) as exc_info:
            convert_voice_note(b"\x00" * 100, duration_s=0.5, min_duration_s=1.0)
        assert exc_info.value.duration_s == 0.5
        assert exc_info.value.min_s == 1.0

    def test_too_long_raises(self):
        with pytest.raises(AudioTooLongError) as exc_info:
            convert_voice_note(b"\x00" * 100, duration_s=700.0, max_duration_s=600.0)
        assert exc_info.value.duration_s == 700.0
        assert exc_info.value.max_s == 600.0

    def test_empty_bytes_raises(self):
        with pytest.raises(AudioConversionError, match="Empty input"):
            convert_voice_note(b"", duration_s=5.0)

    def test_exactly_min_duration_passes_guard(self):
        """Edge case: duration == min should NOT raise AudioTooShortError."""
        # It will fail at FFmpeg (not installed in CI), but should pass the guard
        with pytest.raises((AudioConversionError, FFmpegNotFoundError)):
            convert_voice_note(b"\x00" * 100, duration_s=1.0, min_duration_s=1.0)

    def test_exactly_max_duration_passes_guard(self):
        """Edge case: duration == max should NOT raise AudioTooLongError."""
        with pytest.raises((AudioConversionError, FFmpegNotFoundError)):
            convert_voice_note(b"\x00" * 100, duration_s=600.0, max_duration_s=600.0)


class TestCheckFfmpeg:
    """FFmpeg availability check."""

    def test_ffmpeg_not_found_raises(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(FFmpegNotFoundError):
                check_ffmpeg()

    def test_ffmpeg_found_does_not_raise(self):
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            check_ffmpeg()  # Should not raise


class TestConvertWithMockedFfmpeg:
    """Test the conversion path with a mocked subprocess."""

    def test_successful_conversion(self):
        """Mock FFmpeg returning valid PCM bytes."""
        fake_pcm = b"\x00\x01" * 16000  # 1 second of fake 16-bit samples
        mock_result = type("Result", (), {"returncode": 0, "stdout": fake_pcm, "stderr": b""})()

        with patch("subprocess.run", return_value=mock_result):
            result = convert_voice_note(b"fake_ogg_data", duration_s=5.0)

        assert result.pcm_bytes == fake_pcm
        assert result.sample_rate == 16000
        assert result.channels == 1
        assert result.sample_width == 2

    def test_ffmpeg_nonzero_exit(self):
        """FFmpeg returns non-zero → AudioConversionError."""
        mock_result = type("Result", (), {
            "returncode": 1,
            "stdout": b"",
            "stderr": b"Invalid data found when processing input",
        })()

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(AudioConversionError, match="Invalid data"):
                convert_voice_note(b"corrupt_ogg", duration_s=5.0)

    def test_ffmpeg_empty_output(self):
        """FFmpeg returns 0 but empty stdout → AudioConversionError."""
        mock_result = type("Result", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(AudioConversionError, match="empty output"):
                convert_voice_note(b"fake_ogg", duration_s=5.0)
