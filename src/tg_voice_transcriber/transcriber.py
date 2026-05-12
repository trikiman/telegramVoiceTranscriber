"""Transcription engine: faster-whisper wrapper with warm model and executor offload.

The model is loaded once at startup and kept resident. All transcription calls
are dispatched to a dedicated single-thread executor so the asyncio event loop
is never blocked (CTranslate2 releases the GIL during inference).
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

# Known hallucination phrases whisper produces on silence/noise.
# These are checked case-insensitively against the full transcript.
HALLUCINATION_PHRASES: set[str] = {
    "thanks for watching",
    "thank you for watching",
    "спасибо за просмотр",
    "подписывайтесь на канал",
    "subscribe to the channel",
    "like and subscribe",
    "music",
    "♪",
}

# Allowed languages for detection clamping
ALLOWED_LANGUAGES: frozenset[str] = frozenset({"ru", "en"})


@dataclass(frozen=True)
class TranscriptResult:
    """Result of a successful transcription."""

    text: str
    language: str
    segments_count: int
    duration_s: float = 0.0


@dataclass
class Transcriber:
    """Warm-loaded faster-whisper model with async executor wrapper.

    Usage::

        transcriber = Transcriber(model_size="small", compute_type="int8")
        transcriber.load()  # Call once at startup (blocking, ~2-4s)

        # In async context:
        result = await transcriber.transcribe(pcm_bytes)
    """

    model_size: str = "small"
    compute_type: str = "int8"
    device: str = "cpu"
    cpu_threads: int = 4
    default_language: str = "ru"

    _model: object = field(default=None, init=False, repr=False)
    _executor: ThreadPoolExecutor = field(default=None, init=False, repr=False)

    def load(self) -> None:
        """Load the faster-whisper model into memory.

        Call once at startup. Blocks for 2-4 seconds while the model loads.
        Raises ImportError if faster-whisper is not installed.
        """
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ImportError(
                "faster-whisper is not installed. "
                "Install it: pip install 'faster-whisper>=1.2,<2.0'"
            ) from exc

        log.info(
            "whisper_loading",
            model=self.model_size,
            compute_type=self.compute_type,
            device=self.device,
            cpu_threads=self.cpu_threads,
        )

        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
        )

        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="whisper",
        )

        log.info("whisper_loaded", model=self.model_size)

    def _sync_transcribe(self, pcm_bytes: bytes) -> TranscriptResult:
        """Synchronous transcription — runs in the executor thread.

        Args:
            pcm_bytes: Raw 16 kHz mono s16le PCM bytes.

        Returns:
            TranscriptResult with text, detected language, and segment count.
        """
        import io
        import numpy as np

        if self._model is None:
            raise RuntimeError("Transcriber.load() must be called before transcribe()")

        # Convert raw PCM bytes to float32 numpy array (what faster-whisper expects)
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = self._model.transcribe(
            audio,
            language=None,  # auto-detect
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,  # reduces KV cache growth on long audio
        )

        # Materialize segments (generator → list)
        segment_list = list(segments)

        # Clamp detected language to allowed set
        detected_lang = info.language if info.language in ALLOWED_LANGUAGES else self.default_language

        # Join segment texts
        text = " ".join(seg.text.strip() for seg in segment_list if seg.text.strip())

        # Check for hallucinations
        if self._is_hallucination(text):
            log.debug("hallucination_suppressed", text_preview=text[:50])
            text = ""

        return TranscriptResult(
            text=text,
            language=detected_lang,
            segments_count=len(segment_list),
            duration_s=info.duration,
        )

    async def transcribe(self, pcm_bytes: bytes) -> TranscriptResult:
        """Transcribe PCM audio off the event loop.

        Dispatches to a dedicated single-thread executor so the asyncio
        loop stays responsive during inference.

        Args:
            pcm_bytes: Raw 16 kHz mono s16le PCM bytes from AudioPipeline.

        Returns:
            TranscriptResult with the transcript text and metadata.
        """
        if self._executor is None:
            raise RuntimeError("Transcriber.load() must be called before transcribe()")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._sync_transcribe, pcm_bytes)

    @staticmethod
    def _is_hallucination(text: str) -> bool:
        """Check if the transcript is a known whisper hallucination."""
        if not text:
            return True
        normalized = text.lower().strip()
        if len(normalized) < 3:
            return True
        return any(phrase in normalized for phrase in HALLUCINATION_PHRASES)

    def shutdown(self) -> None:
        """Shut down the executor. Call on application exit."""
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            log.debug("whisper_executor_shutdown")
