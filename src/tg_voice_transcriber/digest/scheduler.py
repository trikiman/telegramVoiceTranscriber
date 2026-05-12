"""Background scheduler: periodically drains buffer, scores, and sends digest."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import structlog
from telethon import TelegramClient

from tg_voice_transcriber.digest import db as digest_db
from tg_voice_transcriber.digest.formatter import format_digest
from tg_voice_transcriber.digest.scorer import DigestScorer

log = structlog.get_logger()


class DigestScheduler:
    """Runs a periodic cycle: drain buffer → score → format → send to delivery chat."""

    def __init__(
        self,
        client: TelegramClient,
        db_path: Path,
        scorer: DigestScorer,
    ) -> None:
        self._client = client
        self._db_path = db_path
        self._scorer = scorer
        self._task: asyncio.Task | None = None
        self._running = False
        self._wake = asyncio.Event()  # allows /digest now to trigger early

    def start(self) -> None:
        """Spawn the background task."""
        self._running = True
        self._task = asyncio.create_task(self._run(), name="digest-scheduler")
        log.info("digest_scheduler_started")

    async def stop(self, grace_period_s: float = 10.0) -> None:
        """Stop the scheduler, waiting for the current cycle to finish."""
        self._running = False
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=grace_period_s)
            except asyncio.TimeoutError:
                self._task.cancel()
                log.warning("digest_scheduler_force_cancelled")
            except asyncio.CancelledError:
                pass
        log.info("digest_scheduler_stopped")

    def wake_now(self) -> None:
        """Trigger an immediate cycle (used by /digest now)."""
        self._wake.set()

    async def _run(self) -> None:
        """Main loop: sleep until next cycle, process buffer."""
        while self._running:
            cfg = await digest_db.load_config(self._db_path)

            # Wait until next cycle, but wake early if triggered or stopped
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=max(1.0, float(cfg.frequency_s)),
                )
            except asyncio.TimeoutError:
                pass

            self._wake.clear()

            if not self._running:
                break

            # Reload config — may have changed during sleep
            cfg = await digest_db.load_config(self._db_path)

            if cfg.paused:
                log.debug("digest_cycle_skipped", reason="paused")
                continue

            if cfg.delivery_chat_id is None:
                log.debug("digest_cycle_skipped", reason="no_delivery_chat")
                continue

            await self._process_cycle(cfg)

    async def _process_cycle(self, cfg: digest_db.DigestConfig) -> None:
        """Drain buffer, score, format, send (if non-empty)."""
        cycle_start = time.time()

        try:
            posts = await digest_db.drain_buffer(self._db_path)
        except Exception:
            log.error("digest_drain_failed", exc_info=True)
            return

        if not posts:
            log.debug("digest_cycle_empty_buffer")
            await digest_db.record_cycle_stats(
                self._db_path,
                cycle_start=cycle_start,
                cycle_end=time.time(),
                msgs_scanned=0,
                msgs_delivered=0,
            )
            return

        log.info("digest_cycle_start", posts=len(posts))

        try:
            scored = await self._scorer.score_batch(posts, cfg.user_prefs_text)
        except Exception:
            log.error("digest_score_failed", exc_info=True, posts=len(posts))
            await digest_db.record_cycle_stats(
                self._db_path,
                cycle_start=cycle_start,
                cycle_end=time.time(),
                msgs_scanned=len(posts),
                msgs_delivered=0,
                llm_error=True,
            )
            return

        cycle_end = time.time()
        messages = format_digest(
            scored,
            threshold=cfg.threshold,
            window_start=cycle_start - cfg.frequency_s,
            window_end=cycle_end,
        )

        delivered = len([p for p in scored if p.score >= cfg.threshold])

        if messages:
            for msg in messages:
                try:
                    await self._client.send_message(
                        cfg.delivery_chat_id,
                        msg,
                        link_preview=False,
                    )
                except Exception:
                    log.error(
                        "digest_send_failed",
                        delivery_chat_hashed=_hashed(cfg.delivery_chat_id),
                        exc_info=True,
                    )
                    break
            log.info(
                "digest_cycle_sent",
                delivered=delivered,
                scanned=len(posts),
                messages=len(messages),
            )
        else:
            log.info(
                "digest_cycle_no_relevant",
                scanned=len(posts),
                threshold=cfg.threshold,
            )

        await digest_db.record_cycle_stats(
            self._db_path,
            cycle_start=cycle_start,
            cycle_end=cycle_end,
            msgs_scanned=len(posts),
            msgs_delivered=delivered,
        )


def _hashed(value: int) -> str:
    import hashlib
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]
