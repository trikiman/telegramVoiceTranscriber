#!/usr/bin/env python3
"""Active VPN Bot Harvester.

Searches Telegram globally for messages promoting VPN trials, evaluates
the offers using the existing OfferJudge, and automatically adds qualifying
bots to the target folder.
Stops once it finds 5 bots, ensuring at least one of them is a 30-day trial.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import structlog
from telethon import TelegramClient

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.llm_failover import create_chat_client
from tg_voice_transcriber.finder.judge import OfferJudge
from tg_voice_transcriber.finder.starter import BotStarter
from tg_voice_transcriber.finder.folder import find_folder_by_title, add_peer_to_folder
from tg_voice_transcriber.finder.links import parse_tme_target

# Configure logging
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
)
log = structlog.get_logger()

BOT_LINK_REGEX = re.compile(r"(?:https?://)?(?:t\.me/|@)([a-zA-Z0-9_]+bot)(?:\?start=([a-zA-Z0-9_-]+))?", re.IGNORECASE)

async def main() -> None:
    print("━" * 50)
    print(" GSD ► Active VPN Bot Harvester")
    print("━" * 50)

    cfg = get_config()
    
    if not cfg.finder_phone:
        print("✗ TG_VOICE_FINDER_PHONE is not set in .env")
        return

    # Use the same session as the finder scheduler
    client = TelegramClient(
        session=str(cfg.finder_session_path),
        api_id=cfg.api_id,
        api_hash=cfg.api_hash.get_secret_value(),
    )

    try:
        await client.start(phone=cfg.finder_phone)
    except Exception as exc:
        print(f"✗ Failed to connect: {exc}")
        print("  Did you get an AuthKeyDuplicatedError? Run scripts/login.py to create a new session!")
        return
        
    print(f"✓ Connected to Telegram as finder account ({cfg.finder_phone}).")

    llm = create_chat_client(cfg, for_finder=True)
    judge = OfferJudge(llm, model=cfg.finder_llm_model)
    starter = BotStarter(client, max_starts_per_hour=20)
    
    target_folder_name = "10 дней vpn"
    folder = await find_folder_by_title(client, target_folder_name)
    if not folder:
        print(f"✗ Target folder '{target_folder_name}' not found!")
        return
        
    print(f"✓ Folder '{target_folder_name}' resolved.")
    
    bots_added = 0
    has_30_day = False
    
    # Global search queries that usually yield VPN ads
    queries = [
        "vpn 30 дней бесплатно",
        "впн 30 дней бесплатно",
        "vpn месяц бесплатно",
        "впн бесплатно",
        "vpn бесплатно"
    ]
    
    # Keep track of bots we've already tried in this run
    seen_bots = set()

    for query in queries:
        if bots_added >= 5 and has_30_day:
            break
            
        print(f"\n▶ Searching globally for: '{query}'")
        
        async for msg in client.iter_messages(None, search=query, limit=30):
            if bots_added >= 5 and has_30_day:
                break
                
            text = msg.text or ""
            if len(text) < 30:
                continue
                
            # Extract possible bot links from the message text
            matches = BOT_LINK_REGEX.findall(text)
            for bot_username, start_token in matches:
                bot_username = bot_username.lower()
                
                # Check if we've already processed this bot
                if bot_username in seen_bots:
                    continue
                seen_bots.add(bot_username)
                
                print(f"  Found bot: @{bot_username}")
                
                # Reconstruct a t.me URL to pass to our parser so it can build the LinkTarget
                simulated_url = f"https://t.me/{bot_username}"
                if start_token:
                    simulated_url += f"?start={start_token}"
                    
                link_target = parse_tme_target(simulated_url)
                
                # Judge the offer text
                judged = await judge.judge_offer(
                    text=text[:1000],
                    channel_id=0, # Global search, no specific channel context needed for judge
                    message_id=msg.id,
                    link_target=link_target
                )
                
                if judged and judged.is_good_trial and not judged.scam_suspected:
                    print(f"    ✓ LLM Approved: {judged.summary}")
                    
                    # File the offer
                    started = await starter.start_and_mute(
                        judged.target_bot or bot_username, 
                        start_param=judged.start_param
                    )
                    
                    if started:
                        try:
                            # Add to folder
                            bot_entity = await client.get_entity(judged.target_bot or bot_username)
                            added = await add_peer_to_folder(
                                client, folder, peer_id=bot_entity.id, access_hash=getattr(bot_entity, "access_hash", None)
                            )
                            if added:
                                print(f"    ✓ Added to folder '{target_folder_name}'")
                                bots_added += 1
                                if judged.trial_days and judged.trial_days >= 30:
                                    has_30_day = True
                                    print("    ★ Found a 30+ day trial!")
                            else:
                                print(f"    ✗ Failed to add to folder.")
                        except Exception as e:
                            print(f"    ✗ Error adding to folder: {e}")
                else:
                    reason = "rejected by LLM" if judged else "LLM failed"
                    if judged and judged.scam_suspected:
                        reason = "scam suspected"
                    print(f"    ✗ Skipped ({reason})")

    print("\n" + "━" * 50)
    print(f"Harvest complete. Added {bots_added}/5 bots.")
    if not has_30_day:
        print("Note: Did not find a 30-day trial in this run.")

if __name__ == "__main__":
    asyncio.run(main())
