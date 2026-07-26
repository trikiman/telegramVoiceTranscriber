#!/usr/bin/env python3
"""Active VPN Bot Harvester (v1.3).

Actively hunts VPN-trial bots across THREE discovery sources and files
qualifying ones into the target folder until the quota is met, guaranteeing at
least one long (30-day) trial:

  1. global-search  — Telegram global message search over VPN queries
  2. channel-feed   — scans your subscribed VPN channels' post feeds
  3. sponsored      — "proxy sponsor" ads on your channels (best-effort)

Each candidate is judged by the existing OfferJudge; good ones are /start-ed
(with the referral token), muted, and added to the folder.

Safety:
  * --dry-run judges + reports candidates WITHOUT sending /start, muting, or
    touching the folder. ALWAYS run this first.
  * The folder is resolved by TITLE once, then pinned by ID in finder.db, so a
    later rename never breaks the pipeline.
  * A FloodWait circuit-breaker and a hard /start budget stop runaway behaviour.

Usage:
    python scripts/harvest_vpn_bots.py --dry-run          # safe preview
    python scripts/harvest_vpn_bots.py                    # live run (5 bots)
    python scripts/harvest_vpn_bots.py --limit 5 --no-sponsored
    TG_VOICE_FINDER_FOLDER_TITLE="my folder" python scripts/harvest_vpn_bots.py
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path

# Ensure the package is importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import structlog
from telethon import TelegramClient

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder import db as finder_db
from tg_voice_transcriber.finder.folder import add_peer_to_folder, resolve_folder
from tg_voice_transcriber.finder.harvest import (
    Candidate,
    FloodBreaker,
    HarvestState,
    extract_bot_links,
    iter_channel_feed_matches,
    iter_global_message_matches,
    iter_sponsored_matches,
    looks_vpnish,
)
from tg_voice_transcriber.finder.judge import OfferJudge
from tg_voice_transcriber.finder.links import parse_tme_target
from tg_voice_transcriber.finder.starter import BotStarter
from tg_voice_transcriber.finder.verify import fetch_bot_welcome
from tg_voice_transcriber.llm_failover import create_chat_client

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(20))  # INFO
log = structlog.get_logger()

BAR = "━" * 56

# Heuristic markers for a subscribe-gate bot (e.g. @TProxyRobot-style: "join
# these channels, then press I've subscribed"). These bots' first reply is
# gate copy, not real trial terms, so the live judge correctly rejects them
# as "no explicit trial claim" — a safe false-negative, not a bug. Tagged
# distinctly so a human can review gate-suspected rejects separately from
# genuinely bad offers, rather than auto-clicking through gates (out of scope
# for this fix — see plan).
_GATE_MARKERS = ("подпишитесь", "подписаться на кан", "я подписался", "check_join")


def _looks_like_gate(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _GATE_MARKERS)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Active VPN Bot Harvester")
    p.add_argument("--dry-run", action="store_true",
                   help="Judge + report only; no /start, mute, or folder writes.")
    p.add_argument("--limit", type=int, default=5,
                   help="Number of bots to collect (default 5).")
    p.add_argument("--no-30day", action="store_true",
                   help="Do not require a 30-day trial among the results.")
    p.add_argument("--max-start", type=int, default=15,
                   help="Hard cap on /start attempts this run (ban safety).")
    p.add_argument("--max-channels", type=int, default=40,
                   help="Max channels to scan for feed/sponsored sources.")
    p.add_argument("--max-chain", type=int, default=25,
                   help="Max bots to follow that were advertised inside other bots.")
    p.add_argument("--no-chain", action="store_true",
                   help="Disable following bots advertised inside other bots.")
    p.add_argument("--recheck-days", type=int, default=14,
                   help="Skip bots already opened within this many days, no "
                        "matter how their ad was reworded (saves /start budget).")
    p.add_argument("--no-search", action="store_true", help="Disable global search source.")
    p.add_argument("--no-feeds", action="store_true", help="Disable channel-feed source.")
    p.add_argument("--no-sponsored", action="store_true", help="Disable sponsored-ad source.")
    return p.parse_args()


async def _process_candidate(
    cand: Candidate,
    *,
    judge: OfferJudge,
    starter: BotStarter,
    client: TelegramClient,
    folder,
    state: HarvestState,
    dry_run: bool,
    db_path,
    chain_out: list[Candidate] | None = None,
    recheck_days: int = 14,
) -> None:
    """Judge one candidate, verify it live, and file it if it holds up.

    Two-stage judging: ad/search/post text is UNRELIABLE marketing copy (live
    verification found bots claiming "30 days free" that were actually 24
    hours, 2 days, or gated behind a review screenshot once really opened —
    see finder/verify.py). Stage 1 judges the discovery-source text as a cheap
    pre-filter. Stage 2 — only reached after `/start`-ing the bot — judges its
    REAL welcome screen, and that verdict is authoritative: filing only
    happens if stage 2 also says good, using the LIVE trial_days/summary (not
    the ad-claimed ones).

    Dedupes against `found_offers` in finder.db BEFORE spending an LLM call OR
    a `/start`: the same (bot, offer text) pair — whether previously FILED or
    previously DEBUNKED at stage 2 — is skipped, so repeated runs never
    re-judge, re-`/start`, or re-fall for the same bad ad text.
    """
    username = cand.username.lstrip("@").lower()
    if state.already_seen(username):
        return
    state.mark_seen(username)

    offer_hash = finder_db.compute_offer_hash(cand.text)
    if await finder_db.offer_already_found(db_path, f"@{username}", offer_hash):
        return
    # Same bot, reworded ad → same bot. Don't spend a /start re-learning it.
    if await finder_db.bot_examined_within(db_path, f"@{username}", recheck_days):
        return

    link_target = parse_tme_target(cand.build_url(), button_text=cand.button_text)
    stage1 = await judge.judge_offer(
        text=cand.text[:1000],
        channel_id=cand.channel_id,
        message_id=cand.message_id,
        link_target=link_target,
    )

    if stage1 is None:
        return

    # A VPN bot whose AD says nothing about a trial is still worth opening.
    # Most sponsored VPN ads are pure hype ("Глушат интернет? СТАРТ 🔥") and
    # state their actual terms only inside the bot — refusing to look unless
    # the ad already promises days is what made this miss real offers a human
    # finds in minutes. Live verification (below) is the real gate, so here we
    # only need "is this plausibly a VPN bot at all", which keeps games/crypto
    # /casino ads out. Costs a /start, so it's bounded by --max-start.
    speculative = False
    if not stage1.is_good_trial or stage1.scam_suspected:
        if stage1.scam_suspected or not looks_vpnish(cand.text, username):
            return
        speculative = True

    bot = (stage1.target_bot or f"@{username}").lstrip("@")
    ad_days = stage1.trial_days

    # Quota already met and we only owe a long trial — skip anything shorter
    # (checked against the AD-claimed days here; re-checked against the LIVE
    # days after verification too, since the ad can both over- and
    # under-state the real offer).
    if not state.should_file(ad_days):
        print(f"    · skip @{bot} ({ad_days}d ad-claimed) — quota met, only a 30-day trial left to find")
        return

    if dry_run:
        # Dry-run never /starts a bot (that would be a write), so it can only
        # preview the UNVERIFIED ad-text judgment — make that explicit.
        state.record_start_attempt()
        state.record_filed(bot, ad_days)
        tag = f"{ad_days}d" if ad_days else "?d"
        star = " ★30-day" if (ad_days and ad_days >= state.long_trial_days) else ""
        if speculative:
            print(f"    ? WOULD OPEN @{bot} — ad states no terms, VPN-ish so worth checking [{cand.source}]")
        else:
            print(f"    ~ WOULD collect @{bot} [{tag}]{star} — {stage1.summary} [{cand.source}] (UNVERIFIED — ad text only)")
        return

    # --- live path: /start (+token) + mute, unconditionally at this point ---
    # This IS the verification step (you must interact with the bot to see
    # its real terms), so the bot is claimed/muted before we know the stage-2
    # verdict. Matches scripts/probe-bots.py's established behavior.
    state.record_start_attempt()
    started = await starter.start_and_mute(stage1.target_bot or username, start_param=stage1.start_param)
    if not started:
        print(f"    ✗ @{bot}: /start failed or rate-limited")
        return

    try:
        entity = await client.get_entity(stage1.target_bot or username)
    except Exception as exc:  # noqa: BLE001
        print(f"    ✗ @{bot}: error resolving entity after /start: {exc}")
        return

    live_text, live_buttons = await fetch_bot_welcome(client, entity)

    # Bots advertise other bots. A VPN bot's welcome screen routinely carries
    # promos for competitors ("NekoBox — получите 15 дней бесплатно"), which is
    # a discovery source that feeds itself: every bot we open can reveal more.
    # Queue them regardless of whether THIS bot's own offer turns out good.
    if chain_out is not None and live_text:
        for chained, token in extract_bot_links(live_text):
            if chained == username or state.already_seen(chained):
                continue
            chain_out.append(
                Candidate(
                    username=chained,
                    start_token=token,
                    text=live_text[:1500],
                    source="bot-chain",
                    channel_id=cand.channel_id,
                )
            )

    stage2 = None
    if live_text:
        stage2 = await judge.judge_offer(
            text=live_text[:1000],
            channel_id=cand.channel_id,
            buttons=live_buttons,
        )

    live_good = stage2 is not None and stage2.is_good_trial and not stage2.scam_suspected

    if not live_good:
        gate_tag = " (gate suspected)" if _looks_like_gate(live_text) else ""
        reason = (
            "no reply from bot" if not live_text
            else (stage2.summary if stage2 else "live judge failed")
        )
        claimed = "ad stated no terms" if speculative else f"ad said {ad_days}d"
        print(f"    ✗ @{bot}: rejected after live check{gate_tag} — {claimed}, real: {reason}")
        # Record so we never re-/start this exact debunked ad text again —
        # keyed by the ORIGINAL ad-text hash, not the live text.
        with contextlib.suppress(Exception):
            await finder_db.record_found_offer(
                db_path,
                target_bot=f"@{bot}",
                offer_hash=offer_hash,
                source_channel_id=cand.channel_id,
                source_message_id=cand.message_id,
                trial_days=stage2.trial_days if stage2 else None,
                trial_price_rub=stage2.trial_price_rub if stage2 else None,
                summary=f"REJECTED after live check (ad claimed {ad_days}d): {reason}"[:200],
                verified_good=False,
            )
        return

    # Live verdict agrees the offer is good — file using the VERIFIED terms.
    live_days = stage2.trial_days
    if not state.should_file(live_days):
        print(f"    · skip @{bot} ({live_days}d verified) — quota met, only a 30-day trial left to find")
        return

    try:
        added = await add_peer_to_folder(
            client, folder, peer_id=entity.id, access_hash=getattr(entity, "access_hash", None)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    ✗ @{bot}: error adding to folder: {exc}")
        return

    state.record_filed(bot, live_days)
    tag = f"{live_days}d" if live_days else "?d"
    star = " ★30-day" if (live_days and live_days >= state.long_trial_days) else ""
    if speculative:
        mismatch = " (ad stated no terms — found by opening it)"
    else:
        mismatch = f" (ad claimed {ad_days}d)" if ad_days != live_days else ""
    if added:
        print(f"    ✓ collected @{bot} [{tag}]{star}{mismatch} — {stage2.summary}")
    else:
        # Already in the folder — count as satisfied so we don't loop forever.
        print(f"    = @{bot} already in folder [{tag}] — {stage2.summary}")

    # Record for dedupe regardless of whether the folder add was new — we
    # never want to re-/start or re-judge this exact offer text again.
    with contextlib.suppress(Exception):
        await finder_db.record_found_offer(
            db_path,
            target_bot=f"@{bot}",
            offer_hash=offer_hash,
            source_channel_id=cand.channel_id,
            source_message_id=cand.message_id,
            trial_days=live_days,
            trial_price_rub=stage2.trial_price_rub,
            summary=stage2.summary,
            verified_good=True,
        )


async def main() -> int:
    args = _parse_args()
    print(BAR)
    print(f"  Active VPN Bot Harvester {'(DRY RUN)' if args.dry_run else ''}".rstrip())
    print(BAR)

    cfg = get_config()

    client = TelegramClient(
        session=str(cfg.finder_session_path),
        api_id=cfg.api_id,
        api_hash=cfg.api_hash.get_secret_value(),
    )

    # Fail FAST on an unauthorized session — never hang on an interactive prompt.
    await client.connect()
    if not await client.is_user_authorized():
        print("✗ Finder session is NOT authorized.")
        print("  Run:  python scripts/login-finder.py   (from your home IP, once)")
        await client.disconnect()
        return 1

    me = await client.get_me()
    print(f"✓ Connected as {me.first_name or ''} (@{me.username or '—'})")

    # LLM (Groq primary + OpenRouter fallback) via the shared factory.
    try:
        llm = create_chat_client(cfg, for_finder=True)
    except ValueError as exc:
        print(f"✗ {exc}")
        await client.disconnect()
        return 1
    judge = OfferJudge(llm, model=cfg.finder_llm_model)
    starter = BotStarter(client, max_starts_per_hour=20)

    # Resolve the target folder: prefer a pinned id (rename-proof), else title.
    title = cfg.finder_folder_title
    pinned_id = None
    try:
        await finder_db.init_finder_db(cfg.finder_db_path)
        db_cfg = await finder_db.load_finder_config(cfg.finder_db_path)
        pinned_id = db_cfg.get("target_folder_id")
    except Exception as exc:  # noqa: BLE001
        log.debug("finder_db_unavailable", error=str(exc))

    folder = await resolve_folder(client, title=title, folder_id=pinned_id)
    if folder is None:
        print(f"✗ Target folder '{title}' not found.")
        print("  Set TG_VOICE_FINDER_FOLDER_TITLE to your exact folder name, or")
        print("  create the folder in Telegram, then re-run.")
        await llm.close()
        await client.disconnect()
        return 1

    # Pin the resolved id so renames never break future runs.
    try:
        await finder_db.set_target_folder(cfg.finder_db_path, title=title, folder_id=folder.id)
    except Exception as exc:  # noqa: BLE001
        log.debug("pin_folder_failed", error=str(exc))
    print(f"✓ Folder '{title}' resolved (id={folder.id}, {len(folder.include_peers)} peers).")

    state = HarvestState(
        target_count=args.limit,
        require_30_day=not args.no_30day,
        max_start_attempts=args.max_start,
    )

    # Sources run FRESHEST FIRST. The /start budget is small and whichever
    # source runs first consumes it, so ordering decides what we ever see.
    # Sponsored ads rotate daily and carry campaigns that exist nowhere else;
    # global message search mostly re-surfaces the same bots our own channels
    # have advertised for weeks, so it goes last as a backfill rather than
    # eating the budget before the fresh sources get a turn.
    sources: list[tuple[str, object]] = []
    if not args.no_sponsored:
        sources.append(("sponsored", iter_sponsored_matches(client, max_channels=args.max_channels)))
    if not args.no_feeds:
        sources.append(("channel-feed", iter_channel_feed_matches(client, max_channels=args.max_channels)))
    if not args.no_search:
        sources.append(("global-search", iter_global_message_matches(client)))

    chain_queue: list[Candidate] = [] if not args.no_chain else None

    stopped = False
    for source_name, gen in sources:
        if stopped:
            break
        print(f"\n▶ Source: {source_name}")
        try:
            async for cand in gen:
                breaker = getattr(client, "_harvest_breaker", None)
                if isinstance(breaker, FloodBreaker) and breaker.tripped:
                    print("  ! FloodWait circuit-breaker tripped — stopping early to protect the account.")
                    stopped = True
                    break
                if state.should_stop():
                    stopped = True
                    break
                if state.budget_exhausted():
                    print(f"  ! Reached /start budget ({state.max_start_attempts}) — stopping.")
                    stopped = True
                    break
                await _process_candidate(
                    cand, judge=judge, starter=starter, client=client,
                    folder=folder, state=state, dry_run=args.dry_run,
                    db_path=cfg.finder_db_path, chain_out=chain_queue,
                    recheck_days=args.recheck_days,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("source_failed", source=source_name, error=str(exc))
        finally:
            aclose = getattr(gen, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):  # pragma: no cover
                    await aclose()

    # --- follow bots advertised inside the bots we just opened --------------
    # Processed last so the primary sources get first claim on the /start
    # budget, and iteratively: a chained bot's own screen can reveal more.
    if chain_queue and not stopped:
        print(f"\n▶ Source: bot-chain ({len(chain_queue)} queued from bot screens)")
        followed = 0
        while chain_queue and followed < args.max_chain:
            breaker = getattr(client, "_harvest_breaker", None)
            if isinstance(breaker, FloodBreaker) and breaker.tripped:
                print("  ! FloodWait circuit-breaker tripped — stopping.")
                break
            if state.should_stop():
                break
            if state.budget_exhausted():
                print(f"  ! Reached /start budget ({state.max_start_attempts}) — stopping.")
                break
            followed += 1
            await _process_candidate(
                chain_queue.pop(0), judge=judge, starter=starter, client=client,
                folder=folder, state=state, dry_run=args.dry_run,
                db_path=cfg.finder_db_path, chain_out=chain_queue,
                    recheck_days=args.recheck_days,
            )

    # --- summary -----------------------------------------------------------
    print("\n" + BAR)
    verb = "Would collect" if args.dry_run else "Collected"
    print(f"  {verb} {state.bots_added}/{state.target_count} bots "
          f"({len(state.seen_bots)} candidates seen, {state.start_attempts} start attempts).")
    if state.require_30_day and not state.has_long_trial:
        print("  ⚠ No 30-day trial found. Try subscribing to more VPN channels first:")
        print("     python scripts/discover-channels.py")
        print("     python scripts/subscribe-channels.py --from-log --commit")
    elif state.has_long_trial:
        print("  ★ Includes at least one 30-day trial.")
    if args.dry_run:
        print("  (dry run — nothing was started, muted, or filed.)")
    print(BAR)

    await llm.close()
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
