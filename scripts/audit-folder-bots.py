#!/usr/bin/env python3
"""Audit sweep: re-verify every bot currently in the target folder.

Live-verification this session found 4 bots already filed via ad/search-text
judging had bait-and-switch real terms (24h claimed as 30d, review-gated,
payment-gated, etc — see the harvester Phase-10 fix in finder/verify.py and
finder/judge.py). This script closes the loop: it re-checks EVERY bot
currently in the folder against its live `/start` welcome screen, and removes
any that are CONFIRMED bad by a real judge verdict.

IMPORTANT — a bot already `/start`-ed weeks ago will often reply to a repeat
`/start` with its ACCOUNT STATE (an active/expired subscription menu, "renew
your plan", etc.), not the original fresh-offer screen. That reply correctly
fails the judge's cheap VPN+trial-marker pre-filter — but that's a "we can't
tell" result, not proof the original offer was fake. Removing on pre-filtered
results wrongly gutted the folder in an earlier live run. So this script keeps
three buckets: GOOD (judge confirms), BAD (judge explicitly rejects a REAL
offer claim — reject the offer, not the absence of one), and INCONCLUSIVE
(pre-filtered or no reply — kept, logged, never auto-removed).

Reuses existing, tested pieces rather than reimplementing:
  - finder/verify.py::fetch_bot_welcome — the live-screen check
  - finder/judge.py::OfferJudge — the same judge (now with conditional-free
    reject rules)
  - finder/folder.py::resolve_folder/find_folder_by_id — folder resolution
  - the clobber-proof re-fetch-then-filter removal pattern from
    scripts/remove-bots-from-folder.py

Usage:
    python scripts/audit-folder-bots.py --dry-run   # report only, no removal
    python scripts/audit-folder-bots.py             # remove CONFIRMED-bad bots
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient
from telethon.tl.functions.contacts import UnblockRequest
from telethon.tl.functions.messages import UpdateDialogFilterRequest

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder import db as finder_db
from tg_voice_transcriber.finder.folder import find_folder_by_id, resolve_folder
from tg_voice_transcriber.finder.judge import OfferJudge
from tg_voice_transcriber.finder.mute import mute_peer
from tg_voice_transcriber.finder.verify import fetch_bot_welcome
from tg_voice_transcriber.llm_failover import create_chat_client

BAR = "━" * 56


def _peer_id(peer) -> int | None:
    return getattr(peer, "user_id", None) or getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Audit + re-verify all bots in the target folder")
    ap.add_argument("--dry-run", action="store_true", help="Report only, never remove.")
    args = ap.parse_args()

    cfg = get_config()
    client = TelegramClient(str(cfg.finder_session_path), cfg.api_id,
                            cfg.api_hash.get_secret_value())
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT AUTHORIZED", file=sys.stderr)
        return 1

    me = await client.get_me()
    print(f"{BAR}\n  Folder audit sweep {'(DRY RUN)' if args.dry_run else ''}\n{BAR}".rstrip())
    print(f"Finder account: {me.first_name}")

    try:
        llm = create_chat_client(cfg, for_finder=True)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        await client.disconnect()
        return 1
    judge = OfferJudge(llm, model=cfg.finder_llm_model)

    await finder_db.init_finder_db(cfg.finder_db_path)
    db_cfg = await finder_db.load_finder_config(cfg.finder_db_path)
    folder = await resolve_folder(client, title=cfg.finder_folder_title,
                                  folder_id=db_cfg.get("target_folder_id"))
    if folder is None:
        print("Folder not found", file=sys.stderr)
        await llm.close()
        await client.disconnect()
        return 1

    peer_ids = [pid for pid in (_peer_id(p) for p in folder.include_peers) if pid is not None]
    print(f"Folder '{cfg.finder_folder_title}' (id={folder.id}) — {len(peer_ids)} peers to audit\n")

    bad: list[str] = []
    good: list[str] = []
    inconclusive: list[str] = []
    unresolved: list[int] = []

    for i, pid in enumerate(peer_ids, 1):
        try:
            entity = await client.get_entity(pid)
        except Exception as exc:
            print(f"  [{i}/{len(peer_ids)}] id={pid}: resolve FAILED — {exc}")
            unresolved.append(pid)
            continue

        uname = getattr(entity, "username", None) or f"id{pid}"

        try:
            await client(UnblockRequest(id=entity))
        except Exception:
            pass

        try:
            await client.send_message(entity, "/start")
        except Exception as exc:
            print(f"  [{i}/{len(peer_ids)}] @{uname}: /start failed ({exc}) — keeping (inconclusive)")
            inconclusive.append(uname)
            continue

        live_text, live_buttons = await fetch_bot_welcome(client, entity)
        await mute_peer(client, entity)

        verdict = None
        if live_text:
            verdict = await judge.judge_offer(text=live_text[:1000], channel_id=0, buttons=live_buttons)

        if verdict is not None and verdict.is_good_trial and not verdict.scam_suspected:
            print(f"  [{i}/{len(peer_ids)}] @{uname}: ✅ OK — {verdict.summary}")
            good.append(uname)
        elif verdict is None or verdict.summary == "(pre-filtered: not a VPN trial)":
            # No reply, or the reply had no VPN+trial marker at all — most
            # likely this bot shows ACCOUNT STATE (already claimed) rather
            # than a fresh offer screen, not proof the original offer was
            # fake. Keep it; a human can spot-check separately if needed.
            reason = "no reply from bot" if not live_text else "no trial claim in reply (likely already-claimed account state)"
            print(f"  [{i}/{len(peer_ids)}] @{uname}: ❔ inconclusive — {reason}")
            inconclusive.append(uname)
        else:
            # The judge saw an explicit offer claim in the live text and
            # rejected IT specifically (conditional-free, too-short, too
            # expensive, scam) — this is real signal, safe to remove.
            print(f"  [{i}/{len(peer_ids)}] @{uname}: ❌ CONFIRMED bad — {verdict.summary}")
            bad.append(uname)

        await asyncio.sleep(random.uniform(4.0, 8.0))

    print(
        f"\n{BAR}\nAudit complete: {len(good)} OK, {len(bad)} confirmed bad, "
        f"{len(inconclusive)} inconclusive (kept), {len(unresolved)} unresolved\n{BAR}".rstrip()
    )

    if bad and not args.dry_run:
        target_ids: set[int] = set()
        for uname in bad:
            try:
                ent = await client.get_entity(uname)
                target_ids.add(ent.id)
            except Exception as exc:
                print(f"  removal resolve FAILED for @{uname}: {exc}")

        live = await find_folder_by_id(client, folder.id)
        if live is not None:
            before = len(live.include_peers)
            live.include_peers = [
                p for p in live.include_peers if _peer_id(p) not in target_ids
            ]
            await client(UpdateDialogFilterRequest(id=live.id, filter=live))
            print(f"Removed {before - len(live.include_peers)} bot(s). Folder: {before} -> {len(live.include_peers)} peers")
    elif bad:
        print(f"(dry run — would remove: {', '.join('@' + u for u in bad)})")

    await llm.close()
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
