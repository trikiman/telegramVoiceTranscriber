"""Job queue for voice-note transcription tasks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Job:
    """A single voice-note transcription job."""

    chat_id: int
    msg_id: int
    sender_id: int
    direction: str  # "incoming" or "outgoing"
    voice_duration_s: float
    enqueued_at: datetime = field(default_factory=datetime.utcnow)


def create_queue(maxsize: int = 10) -> asyncio.Queue[Job]:
    """Create a bounded asyncio queue for voice-note jobs."""
    return asyncio.Queue(maxsize=maxsize)
