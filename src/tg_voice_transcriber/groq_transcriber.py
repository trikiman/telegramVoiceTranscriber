"""Groq Whisper transcriber — thin wrapper around the shared GroqClient."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from tg_voice_transcriber.groq_client import GroqClient
from tg_voice_transcriber.transcriber import (
    ALLOWED_LANGUAGES,
    HALLUCINATION_PHRASES,
    TranscriptResult,
)

log = structlog.get_logger()

DEFAULT_MODEL = "whisper-large-v3-turbo"


@dataclass
class GroqTranscriber:
    """Transcription via Groq Whisper, built on a shared GroqClient."""

    groq: GroqClient  # shared client (handles key rotation)
    model: str = DEFAULT_MODEL
    default_language: str = "ru"

    # Back-compat: construct with api_keys string for legacy callers
    api_keys: str | None = None

    _owns_client: bool = field(default=False, init=False, repr=False)

    def load(self) -> None:
        """Initialize. If api_keys was passed and groq is None, build a client internally."""
        if self.groq is None:
            if not self.api_keys:
                raise ValueError("Either groq or api_keys must be provided")
            self.groq = GroqClient(api_keys=self.api_keys)
            self.groq.load()
            self._owns_client = True

    async def transcribe_ogg(
        self,
        ogg_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptResult:
        """Transcribe OGG/Opus bytes via Groq."""
        lang = language if language in ALLOWED_LANGUAGES else self.default_language

        payload = await self.groq.transcribe_ogg(
            ogg_bytes, language=lang, model=self.model
        )

        text = (payload.get("text") or "").strip()
        detected_lang = payload.get("language", lang)
        duration = float(payload.get("duration", 0.0))
        segments = payload.get("segments") or []

        if detected_lang not in ALLOWED_LANGUAGES:
            detected_lang = lang

        if self._is_hallucination(text):
            log.debug("hallucination_suppressed_groq", text_preview=text[:50])
            text = ""

        return TranscriptResult(
            text=text,
            language=detected_lang,
            segments_count=len(segments),
            duration_s=duration,
        )

    async def transcribe(self, pcm_bytes: bytes) -> TranscriptResult:
        """Compatibility shim — callers should prefer transcribe_ogg()."""
        return await self.transcribe_ogg(pcm_bytes, language=self.default_language)

    async def close(self) -> None:
        """Close the underlying client if we own it."""
        if self._owns_client and self.groq is not None:
            await self.groq.close()

    def shutdown(self) -> None:
        """Best-effort sync shutdown (for interface compatibility)."""
        import asyncio
        if self._owns_client and self.groq is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.groq.close())
            except RuntimeError:
                pass

    @staticmethod
    def _is_hallucination(text: str) -> bool:
        if not text:
            return True
        normalized = text.lower().strip()
        if len(normalized) < 3:
            return True
        return any(phrase in normalized for phrase in HALLUCINATION_PHRASES)
