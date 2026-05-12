"""Entry point: ``python -m tg_voice_transcriber``.

Connects to Telegram, loads the whisper model, registers event handlers,
starts the worker, and idles until interrupted.
"""

from __future__ import annotations

import asyncio
import sys

import structlog

from tg_voice_transcriber.audio import FFmpegNotFoundError, check_ffmpeg
from tg_voice_transcriber.client import SessionInvalidError, TelegramUserbot
from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.digest import db as digest_db
from tg_voice_transcriber.digest.commands import register_command_handlers
from tg_voice_transcriber.digest.ingest import register_digest_handler
from tg_voice_transcriber.digest.scheduler import DigestScheduler
from tg_voice_transcriber.digest.scorer import DigestScorer
from tg_voice_transcriber.formatter import format_placeholder
from tg_voice_transcriber.groq_client import GroqClient
from tg_voice_transcriber.groq_transcriber import GroqTranscriber
from tg_voice_transcriber.handlers import register_handlers
from tg_voice_transcriber.logging import configure_logging
from tg_voice_transcriber.queue import Job, create_queue
from tg_voice_transcriber.reply import ReplyService
from tg_voice_transcriber.transcriber import Transcriber
from tg_voice_transcriber.worker import Worker

log = structlog.get_logger()

# Exit code 78 = EX_CONFIG (BSD sysexits) — "configuration error".
EX_CONFIG = 78


async def main() -> None:
    """Connect, load model, register handlers, start worker, idle."""
    cfg = get_config()
    configure_logging(cfg.log_level)

    # Fail-fast: check FFmpeg is available (only needed for local whisper path)
    if not cfg.use_groq:
        try:
            check_ffmpeg()
        except FFmpegNotFoundError as exc:
            log.error("startup_failed", reason=str(exc))
            sys.exit(EX_CONFIG)

    # Validate Groq config if using it
    if cfg.use_groq:
        if cfg.groq_api_key is None or not cfg.groq_api_key.get_secret_value():
            log.error("startup_failed", reason="TG_VOICE_USE_GROQ=true but TG_VOICE_GROQ_API_KEY is not set")
            sys.exit(EX_CONFIG)

    # Connect to Telegram
    userbot = TelegramUserbot(cfg)
    try:
        await userbot.start()
    except SessionInvalidError as exc:
        log.error(
            "auth_required",
            reason=exc.reason,
            hint="Run `python scripts/login.py` on your local machine to create a valid session.",
        )
        sys.exit(EX_CONFIG)

    # Build shared Groq client if enabled (both transcription and future digest use it)
    groq_client: GroqClient | None = None
    if cfg.use_groq:
        groq_client = GroqClient(api_keys=cfg.groq_api_key.get_secret_value())
        try:
            groq_client.load()
        except ImportError as exc:
            log.error("startup_failed", reason=str(exc))
            await userbot.stop()
            sys.exit(EX_CONFIG)

    # Load transcriber (Groq = instant, local = blocking 2-4s)
    transcriber: Transcriber | GroqTranscriber
    if cfg.use_groq:
        transcriber = GroqTranscriber(
            groq=groq_client,
            model=cfg.groq_model,
            default_language=cfg.default_language or "ru",
        )
        try:
            transcriber.load()
        except ImportError as exc:
            log.error("startup_failed", reason=str(exc))
            await userbot.stop()
            sys.exit(EX_CONFIG)
    else:
        transcriber = Transcriber(
            model_size="small",
            compute_type="int8",
            device="cpu",
            cpu_threads=2,  # 2-vCPU VPS
            default_language=cfg.default_language or "ru",
        )
        try:
            transcriber.load()
        except ImportError as exc:
            log.error("startup_failed", reason=str(exc))
            await userbot.stop()
            sys.exit(EX_CONFIG)
        except Exception as exc:
            log.error("model_load_failed", reason=str(exc), exc_info=True)
            await userbot.stop()
            sys.exit(EX_CONFIG)

    # Set up queue, reply service, worker
    queue: asyncio.Queue[Job] = create_queue(maxsize=cfg.queue_maxsize)
    reply_service = ReplyService(userbot.client)
    worker = Worker(
        queue=queue,
        reply_service=reply_service,
        transcriber=transcriber,
        config=cfg,
        client=userbot.client,
        use_groq=cfg.use_groq,
    )

    # Register event handlers
    register_handlers(userbot.client, queue, max_age_seconds=600.0)

    # Start worker
    worker.start()

    identity = await userbot.who_am_i()
    log.info(
        "ready",
        connected_as=f"@{identity}",
        queue_maxsize=cfg.queue_maxsize,
        backend="groq" if cfg.use_groq else "local_whisper",
        language=cfg.default_language or "auto",
    )

    # Set up channel digest (v1.1) — only if Groq is available for the LLM calls
    digest_scheduler: DigestScheduler | None = None
    if cfg.digest_enabled and cfg.use_groq and groq_client is not None:
        try:
            await digest_db.init_db(cfg.digest_db_path)
            digest_cfg = await digest_db.load_config(cfg.digest_db_path)

            scorer = DigestScorer(groq_client, model=cfg.digest_llm_model)
            digest_scheduler = DigestScheduler(
                client=userbot.client,
                db_path=cfg.digest_db_path,
                scorer=scorer,
            )

            register_digest_handler(
                userbot.client,
                cfg.digest_db_path,
                default_track_all=cfg.digest_default_track_all,
            )
            register_command_handlers(
                userbot.client,
                cfg.digest_db_path,
                digest_scheduler,
            )
            digest_scheduler.start()

            log.info(
                "digest_ready",
                db_path=str(cfg.digest_db_path),
                configured=digest_cfg.ready,
                paused=digest_cfg.paused,
                frequency_s=digest_cfg.frequency_s,
                threshold=digest_cfg.threshold,
            )
        except Exception:
            log.error("digest_init_failed", exc_info=True)
            digest_scheduler = None
    else:
        log.info(
            "digest_disabled",
            digest_enabled=cfg.digest_enabled,
            use_groq=cfg.use_groq,
        )

    # Set up graceful shutdown on SIGTERM (systemd sends this on stop/restart)
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("signal_received", signal="SIGTERM")
        shutdown_event.set()

    import signal
    import sys as _sys

    if _sys.platform != "win32":
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
        loop.add_signal_handler(signal.SIGINT, _signal_handler)

    # Idle until disconnected or shutdown signal
    try:
        disconnect_task = asyncio.create_task(
            userbot.client.run_until_disconnected(), name="telegram-idle"
        )
        shutdown_task = asyncio.create_task(shutdown_event.wait(), name="shutdown-wait")

        done, pending = await asyncio.wait(
            [disconnect_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

        if shutdown_event.is_set():
            log.info("shutting_down", reason="signal")
        else:
            log.info("shutting_down", reason="disconnected")

    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutting_down", reason="interrupted")
    finally:
        await worker.stop(grace_period_s=cfg.grace_period_s)
        if digest_scheduler is not None:
            await digest_scheduler.stop(grace_period_s=cfg.grace_period_s)
        if isinstance(transcriber, GroqTranscriber):
            await transcriber.close()
        else:
            transcriber.shutdown()
        if groq_client is not None:
            await groq_client.close()
        await userbot.stop()


def _sync_main() -> None:
    """Synchronous wrapper for use as a console_scripts entry point."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    _sync_main()
