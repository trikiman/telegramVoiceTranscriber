"""Audio pipeline: OGG/Opus voice-note bytes → 16 kHz mono s16le PCM.

Uses FFmpeg as a subprocess, piping via stdin/stdout (voice notes are <1 MB,
so in-memory is fine). No temp files are created.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import structlog

log = structlog.get_logger()


class FFmpegNotFoundError(RuntimeError):
    """Raised at startup if FFmpeg is not on PATH."""

    def __init__(self) -> None:
        super().__init__(
            "FFmpeg not found on PATH. Install it: `apt install ffmpeg` (Ubuntu) "
            "or `brew install ffmpeg` (macOS)."
        )


class AudioTooShortError(ValueError):
    """Voice note is shorter than the configured minimum duration."""

    def __init__(self, duration_s: float, min_s: float) -> None:
        self.duration_s = duration_s
        self.min_s = min_s
        super().__init__(f"Audio too short: {duration_s:.1f}s < {min_s:.1f}s minimum")


class AudioTooLongError(ValueError):
    """Voice note exceeds the configured maximum duration."""

    def __init__(self, duration_s: float, max_s: float) -> None:
        self.duration_s = duration_s
        self.max_s = max_s
        super().__init__(f"Audio too long: {duration_s:.1f}s > {max_s:.1f}s maximum")


class AudioConversionError(RuntimeError):
    """FFmpeg failed to convert the audio."""

    def __init__(self, stderr: str) -> None:
        self.stderr = stderr
        super().__init__(f"FFmpeg conversion failed: {stderr[:200]}")


@dataclass(frozen=True)
class PCMResult:
    """Result of a successful audio conversion."""

    pcm_bytes: bytes
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2  # 16-bit = 2 bytes per sample


def check_ffmpeg() -> None:
    """Verify FFmpeg is available. Call once at startup.

    Raises:
        FFmpegNotFoundError: if ``ffmpeg`` is not on PATH.
    """
    if shutil.which("ffmpeg") is None:
        raise FFmpegNotFoundError()
    log.debug("ffmpeg_found", path=shutil.which("ffmpeg"))


def convert_voice_note(
    ogg_bytes: bytes,
    duration_s: float,
    *,
    min_duration_s: float = 1.0,
    max_duration_s: float = 600.0,
) -> PCMResult:
    """Convert OGG/Opus voice-note bytes to 16 kHz mono s16le PCM.

    Args:
        ogg_bytes: Raw bytes of the OGG/Opus file (from Telegram download).
        duration_s: Duration in seconds (from Telegram's DocumentAttributeAudio.duration).
        min_duration_s: Minimum acceptable duration. Shorter → AudioTooShortError.
        max_duration_s: Maximum acceptable duration. Longer → AudioTooLongError.

    Returns:
        PCMResult with the raw PCM bytes.

    Raises:
        AudioTooShortError: if duration < min_duration_s.
        AudioTooLongError: if duration > max_duration_s.
        AudioConversionError: if FFmpeg fails.
        FFmpegNotFoundError: if FFmpeg is not on PATH (shouldn't happen if check_ffmpeg() was called at startup).
    """
    # Duration guards — check BEFORE invoking FFmpeg to save CPU
    if duration_s < min_duration_s:
        raise AudioTooShortError(duration_s, min_duration_s)
    if duration_s > max_duration_s:
        raise AudioTooLongError(duration_s, max_duration_s)

    if not ogg_bytes:
        raise AudioConversionError("Empty input bytes")

    # FFmpeg command: read OGG from stdin, output raw 16kHz mono s16le PCM to stdout
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", "pipe:0",       # read from stdin
        "-ac", "1",           # mono
        "-ar", "16000",       # 16 kHz
        "-f", "s16le",        # raw signed 16-bit little-endian
        "-acodec", "pcm_s16le",
        "pipe:1",             # write to stdout
    ]

    try:
        result = subprocess.run(
            cmd,
            input=ogg_bytes,
            capture_output=True,
            timeout=max(30, duration_s * 2),  # generous timeout
        )
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError() from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioConversionError(f"FFmpeg timed out after {exc.timeout}s") from exc

    if result.returncode != 0:
        raise AudioConversionError(result.stderr.decode(errors="replace"))

    pcm = result.stdout
    if not pcm:
        raise AudioConversionError("FFmpeg produced empty output")

    log.debug(
        "audio_converted",
        input_bytes=len(ogg_bytes),
        output_bytes=len(pcm),
        duration_s=duration_s,
    )

    return PCMResult(pcm_bytes=pcm)
