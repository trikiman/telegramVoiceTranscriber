"""Format transcription results into Telegram reply text."""

from __future__ import annotations

from tg_voice_transcriber.transcriber import TranscriptResult

# Telegram message length limit
MAX_MESSAGE_LENGTH = 4096


def format_transcript(result: TranscriptResult) -> list[str]:
    """Format a TranscriptResult into one or more reply messages.

    Returns a list of strings. Usually one element, but splits into
    multiple if the transcript exceeds Telegram's 4096-char limit.

    Empty/silence transcripts return a single "(silence)" message.
    """
    if not result.text:
        return ["(silence)"]

    text = result.text.strip()

    # Single message fits
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    # Split into chunks at word boundaries
    chunks: list[str] = []
    while text:
        if len(text) <= MAX_MESSAGE_LENGTH:
            chunks.append(text)
            break

        # Find last space before the limit
        split_at = text.rfind(" ", 0, MAX_MESSAGE_LENGTH)
        if split_at == -1:
            # No space found — hard split
            split_at = MAX_MESSAGE_LENGTH

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()

    return chunks


def format_placeholder() -> str:
    """Return the placeholder text shown while transcription is in progress."""
    return "⏳ Transcribing…"


def format_error() -> str:
    """Return the error text shown when transcription fails."""
    return "❌ transcription failed"
