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
from tg_voice_transcriber.formatter import format_placeholder
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

    # Fail-fast: check FFmpeg is available
    try:
        check_ffmpeg()
    except FFmpegNotFoundError as exc:
        log.error("startup_failed", reason=str(exc))
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

    # Load whisper model (blocking, ~2-4s)
    transcriber = Transcriber(
        model_size="small",
        compute_type="int8",
        device="cpu",
        cpu_threads=4,
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
    )

    # Register event handlers
    register_handlers(userbot.client, queue, max_age_seconds=600.0)

    # Start worker
    worker.start()

    identity = await userbot.who_am_i()
    log.info("ready", connected_as=f"@{identity}", queue_maxsize=cfg.queue_maxsize)

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
        transcriber.shutdown()
        await userbot.stop()


def _sync_main() -> None:
    """Synchronous wrapper for use as a console_scripts entry point."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    _sync_main()
