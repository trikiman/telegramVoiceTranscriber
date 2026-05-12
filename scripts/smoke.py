#!/usr/bin/env python3
"""Smoke test for the audio pipeline.

Usage:
    python scripts/smoke.py path/to/voice.ogg [duration_seconds]

If duration_seconds is omitted, ffprobe is used to detect it.
Prints the PCM byte count and first few sample values.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

# Ensure the package is importable when running from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tg_voice_transcriber.audio import (  # noqa: E402
    AudioConversionError,
    AudioTooLongError,
    AudioTooShortError,
    PCMResult,
    check_ffmpeg,
    convert_voice_note,
)


def get_duration_ffprobe(file_path: Path) -> float:
    """Get audio duration using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ffprobe failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return float(result.stdout.strip())


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/smoke.py <file.ogg> [duration_seconds]")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    # Duration: from CLI arg or ffprobe
    if len(sys.argv) >= 3:
        duration_s = float(sys.argv[2])
    else:
        duration_s = get_duration_ffprobe(file_path)

    print(f"Input: {file_path} ({file_path.stat().st_size} bytes, {duration_s:.2f}s)")

    check_ffmpeg()

    ogg_bytes = file_path.read_bytes()

    try:
        result: PCMResult = convert_voice_note(ogg_bytes, duration_s)
    except (AudioTooShortError, AudioTooLongError, AudioConversionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Print stats
    num_samples = len(result.pcm_bytes) // result.sample_width
    computed_duration = num_samples / result.sample_rate
    print(f"Output: {len(result.pcm_bytes)} bytes PCM ({num_samples} samples, {computed_duration:.2f}s)")
    print(f"Format: {result.sample_rate} Hz, {result.channels} channel(s), {result.sample_width * 8}-bit signed LE")

    # Print first 10 sample values
    first_samples = struct.unpack_from(f"<{min(10, num_samples)}h", result.pcm_bytes)
    print(f"First samples: {list(first_samples)}")


if __name__ == "__main__":
    main()
