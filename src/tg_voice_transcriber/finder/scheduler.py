"""VPN Trial Finder scheduler: scans channels, judges offers, files good ones.

This is the main finder loop. Every cycle (default hourly):
1. Fetch sponsored messages from tracked channels (5-min cached)
2. Judge each offer with the LLM
3. For good offers: /start the bot, mute it, add to folder, notify
4. Dedupe via found_offers table
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path

import structlog
from telethon import TelegramClient
from telethon.errors import FloodWaitError

from tg_voice_transcriber.finder import db as finder_db
from tg_voice_transcriber.finder.folder import add_peer_to_folder, resolve_folder
from tg_voice_transcriber.finder.judge import OfferJudge
from tg_voice_transcriber.finder.links import parse_tme_target
from tg_voice_transcriber.finder.sponsored import SponsoredMessageFetcher
from tg_voice_transcriber.finder.starter import BotStarter

log = structlog.get_logger()


class FinderScheduler:
    """Background scheduler: scans VPN channels, finds good trials, files them."""

    def __init__(
        self,
        client: TelegramClient,
        db_path: Path,
        judge: OfferJudge,
        tracked_channel_ids: list[int],
        folder_title_fallback: str | None = None,
    ) -> None:
        self._client = client
        self._db_path = db_path
        self._judge = judge
        self._tracked_channels = tracked_channel_ids
        # Config-level folder title used to auto-heal a stale DB title (e.g. the
        # folder was renamed after the DB row was written).
        self._folder_title_fallback = folder_title_fallback
        self._sponsored_fetcher = SponsoredMessageFetcher(client)
        self._starter = BotStarter(client, max_starts_per_hour=10)
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        """Spawn the background task."""
        self._running = True
        self._task = asyncio.create_task(self._run(), name="finder-scheduler")
        log.info("finder_scheduler_started", channels=len(self._tracked_channels))

    async def stop(self, grace_period_s: float = 10.0) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=grace_period_s)
            except asyncio.TimeoutError:
                self._task.cancel()
                log.warning("finder_scheduler_force_cancelled")
            except asyncio.CancelledError:
                pass
        log.info("finder_scheduler_stopped")

    async def _run(self) -> None:
        """Main loop: sleep until next cycle, scan channels."""
        while self._running:
            config = await finder_db.load_finder_config(self._db_path)

            # Wait for next cycle
            try:
                await asyncio.sleep(max(60.0, float(config["scan_interval_s"])))
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            config = await finder_db.load_finder_config(self._db_path)

            if not config["enabled"]:
                log.debug("finder_cycle_skipped", reason="disabled")
                continue

            try:
                await self._process_cycle(config)
            except FloodWaitError as exc:
                # Respect Telegram's back-off; never hammer retries (ban risk).
                wait_s = int(getattr(exc, "seconds", 60))
                log.warning("finder_cycle_floodwait", seconds=wait_s)
                try:
                    await asyncio.sleep(min(wait_s, 3600))
                except asyncio.CancelledError:
                    break
            except Exception:
                log.error("finder_cycle_error", exc_info=True)

    async def _process_cycle(self, config: dict) -> None:
        """Scan channels, judge offers, file good ones."""
        log.info("finder_cycle_start", channels=len(self._tracked_channels))

        # Resolve target folder once per cycle. Prefer the pinned id (rename-
        # proof); fall back to the DB title, then to the config-level title.
        folder = await resolve_folder(
            self._client,
            title=config["target_folder_title"],
            folder_id=config.get("target_folder_id"),
        )
        if (
            folder is None
            and self._folder_title_fallback
            and self._folder_title_fallback != config["target_folder_title"]
        ):
            folder = await resolve_folder(self._client, title=self._folder_title_fallback)
            if folder is not None:
                config["target_folder_title"] = self._folder_title_fallback

        if folder is None:
            log.error(
                "finder_folder_not_found",
                title=config["target_folder_title"],
                fallback=self._folder_title_fallback,
            )
            return

        # Auto-heal: pin the resolved id (and any healed title) so future cycles
        # are immune to renames and skip the by-name scan.
        if config.get("target_folder_id") != folder.id:
            config["target_folder_id"] = folder.id
            try:
                await finder_db.save_finder_config(self._db_path, config)
                log.info("finder_folder_pinned", folder_id=folder.id, title=config["target_folder_title"])
            except Exception as exc:  # noqa: BLE001
                log.warning("finder_folder_pin_failed", error=str(exc))

        good_offers_filed = 0
        offers_scanned = 0

        # Slow rotation through channels with random delays
        for channel_id in self._tracked_channels:
            if not self._running:
                break

            # Small delay between channels to spread load
            await asyncio.sleep(random.uniform(2.0, 5.0))

            # Fetch sponsored messages (5-min cached)
            sponsored = await self._sponsored_fetcher.fetch_sponsored_messages(channel_id)

            for ad in sponsored:
                if not self._running:
                    break

                offers_scanned += 1
                text = ad["message"] or ""
                # Offer text is often only in the ad title — include it as context.
                title = ad.get("title")
                judge_text = f"{title}\n{text}".strip() if title else text
                if not judge_text or len(judge_text) < 15:
                    continue

                # Deterministically resolve the deep-link target (bot + start
                # token) from the ad url/button — the LLM only judges quality.
                link_target = parse_tme_target(
                    ad.get("url"), button_text=ad.get("button_text")
                )

                # Judge the offer
                judged = await self._judge.judge_offer(
                    text=judge_text,
                    channel_id=channel_id,
                    message_id=None,  # ads don't have message_id
                    link_target=link_target,
                )

                if judged is None:
                    continue

                # Skip if not a good trial or if scam
                if not judged.is_good_trial or judged.scam_suspected:
                    log.debug(
                        "finder_offer_rejected",
                        good=judged.is_good_trial,
                        scam=judged.scam_suspected,
                        summary=judged.summary[:50],
                    )
                    continue

                # Skip if no bot extracted
                if not judged.target_bot:
                    log.debug("finder_no_bot", summary=judged.summary[:50])
                    continue

                # Dedupe
                offer_hash = finder_db.compute_offer_hash(judge_text)
                if await finder_db.offer_already_found(self._db_path, judged.target_bot, offer_hash):
                    log.debug("finder_duplicate", bot=judged.target_bot)
                    continue

                # Good offer — file it
                success = await self._file_offer(judged, folder)
                if success:
                    good_offers_filed += 1
                    await finder_db.record_found_offer(
                        self._db_path,
                        target_bot=judged.target_bot,
                        offer_hash=offer_hash,
                        source_channel_id=channel_id,
                        source_message_id=None,
                        trial_days=judged.trial_days,
                        trial_price_rub=judged.trial_price_rub,
                        summary=judged.summary,
                    )

        log.info(
            "finder_cycle_complete",
            scanned=offers_scanned,
            filed=good_offers_filed,
        )

    async def _file_offer(self, offer, folder) -> bool:
        """File a good offer: /start, mute, add to folder, notify."""
        bot_username = offer.target_bot.lstrip("@")

        # Step 1: /start (with referral token to claim the trial) + mute
        started = await self._starter.start_and_mute(
            bot_username, start_param=offer.start_param
        )
        if not started:
            log.warning("finder_start_failed", bot=bot_username)
            return False

        # Step 2: Add to folder
        try:
            bot_entity = await self._client.get_entity(bot_username)
            added = await add_peer_to_folder(
                self._client,
                folder,
                peer_id=bot_entity.id,
                access_hash=getattr(bot_entity, "access_hash", None),
            )
            if not added:
                log.warning("finder_folder_add_failed", bot=bot_username)
        except Exception as exc:
            log.error("finder_folder_add_error", bot=bot_username, error=str(exc))

        # Step 3: Notify to Saved Messages
        try:
            me = await self._client.get_me()
            await self._client.send_message(
                me.id,
                f"✅ {offer.summary}",
                link_preview=False,
            )
        except Exception as exc:
            log.error("finder_notify_failed", bot=bot_username, error=str(exc))

        log.info("finder_offer_filed", bot=bot_username, summary=offer.summary)
        return True
