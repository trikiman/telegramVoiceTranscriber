"""Worker coroutine: pulls jobs from the queue and orchestrates the pipeline."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog

from tg_voice_transcriber.audio import (
    AudioConversionError,
    AudioTooLongError,
    AudioTooShortError,
    convert_voice_note,
)
from tg_voice_transcriber.config import Config
from tg_voice_transcriber.formatter import format_error, format_placeholder, format_transcript
from tg_voice_transcriber.queue import Job
from tg_voice_transcriber.reply import ReplyService
from tg_voice_transcriber.transcriber import Transcriber

log = structlog.get_logger()


class Worker:
    """Single-consumer worker that processes voice-note jobs sequentially.

    Supports two transcription backends:
    - Groq API (cloud): audio bytes sent directly, no FFmpeg needed
    - Local faster-whisper: OGG → FFmpeg → PCM → whisper

    Pipeline per job:
    1. Send placeholder reply ("⏳ Transcribing…")
    2. Download voice-note bytes from Telegram
    3. Transcribe (Groq direct, or FFmpeg→PCM→local)
    4. Edit placeholder with final transcript (or error)
    """

    def __init__(
        self,
        queue: asyncio.Queue[Job],
        reply_service: ReplyService,
        transcriber,  # Transcriber | GroqTranscriber — duck-typed
        config: Config,
        client,  # TelegramClient — for downloading media
        *,
        use_groq: bool = False,
    ) -> None:
        self._queue = queue
        self._reply = reply_service
        self._transcriber = transcriber
        self._config = config
        self._client = client
        self._use_groq = use_groq
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the worker as a background task."""
        self._running = True
        self._task = asyncio.create_task(self._run(), name="voice-worker")
        log.info("worker_started")

    async def stop(self, grace_period_s: float = 10.0) -> None:
        """Stop the worker, waiting for in-flight job to finish."""
        self._running = False
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=grace_period_s)
            except asyncio.TimeoutError:
                self._task.cancel()
                log.warning("worker_force_cancelled", grace_period_s=grace_period_s)
            except asyncio.CancelledError:
                pass
        log.info("worker_stopped")

    async def _run(self) -> None:
        """Main loop: pull jobs and process them."""
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._process_job(job)
            except Exception:
                log.error("job_unhandled_error", job_msg_id=job.msg_id, exc_info=True)
            finally:
                self._queue.task_done()

    async def _process_job(self, job: Job) -> None:
        """Process a single voice-note job through the full pipeline."""
        job_log = log.bind(
            chat_id=job.chat_id,
            msg_id=job.msg_id,
            direction=job.direction,
            duration_s=job.voice_duration_s,
        )

        # 1. Send placeholder
        placeholder_id = await self._reply.send_reply(
            job.chat_id, format_placeholder(), reply_to=job.msg_id
        )
        if placeholder_id is None:
            job_log.warning("placeholder_send_failed")
            return

        try:
            # 2. Download voice-note bytes
            voice_bytes = await self._download_voice(job)
            if voice_bytes is None:
                await self._reply.edit_message(job.chat_id, placeholder_id, format_error())
                return

            # 3+4. Transcribe — two paths depending on backend
            if self._use_groq:
                # Duration guards (still useful to avoid burning Groq quota on bad audio)
                if job.voice_duration_s < self._config.min_voice_duration_s:
                    raise AudioTooShortError(job.voice_duration_s, self._config.min_voice_duration_s)
                if job.voice_duration_s > self._config.max_voice_duration_s:
                    raise AudioTooLongError(job.voice_duration_s, self._config.max_voice_duration_s)

                # Groq accepts OGG directly — no FFmpeg step
                transcript = await self._transcriber.transcribe_ogg(
                    voice_bytes,
                    language=self._config.default_language or None,
                )
            else:
                # Local path: OGG → FFmpeg → PCM → local whisper
                pcm_result = convert_voice_note(
                    voice_bytes,
                    job.voice_duration_s,
                    min_duration_s=self._config.min_voice_duration_s,
                    max_duration_s=self._config.max_voice_duration_s,
                )
                transcript = await self._transcriber.transcribe(pcm_result.pcm_bytes)

            # 5. Format and edit placeholder
            parts = format_transcript(transcript)
            # Edit the placeholder with the first part
            await self._reply.edit_message(job.chat_id, placeholder_id, parts[0])

            # Send additional parts as separate replies (for long transcripts)
            for part in parts[1:]:
                await self._reply.send_reply(job.chat_id, part, reply_to=job.msg_id)

            job_log.info(
                "transcription_complete",
                language=transcript.language,
                chars=len(transcript.text),
                segments=transcript.segments_count,
            )

        except AudioTooShortError:
            # Too short — edit placeholder to indicate
            await self._reply.edit_message(job.chat_id, placeholder_id, "(too short)")
            job_log.debug("voice_too_short")

        except AudioTooLongError as exc:
            await self._reply.edit_message(
                job.chat_id,
                placeholder_id,
                f"(too long to transcribe, >{exc.max_s / 60:.0f} min)",
            )
            job_log.info("voice_too_long", duration_s=exc.duration_s)

        except AudioConversionError as exc:
            await self._reply.edit_message(job.chat_id, placeholder_id, format_error())
            job_log.error("audio_conversion_failed", error=str(exc))

        except Exception:
            await self._reply.edit_message(job.chat_id, placeholder_id, format_error())
            job_log.error("transcription_failed", exc_info=True)

    async def _download_voice(self, job: Job) -> bytes | None:
        """Download voice-note bytes from Telegram."""
        import io

        try:
            buffer = io.BytesIO()
            await self._client.download_media(
                await self._client.get_messages(job.chat_id, ids=job.msg_id),
                file=buffer,
            )
            data = buffer.getvalue()
            if not data:
                log.warning("download_empty", msg_id=job.msg_id)
                return None
            return data
        except Exception:
            log.error("download_failed", msg_id=job.msg_id, exc_info=True)
            return None
