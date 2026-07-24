#!/usr/bin/env python3
"""Test script: verify judge.py can evaluate VPN trial offers.

Usage:
    python scripts/test-judge.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.judge import OfferJudge
from tg_voice_transcriber.groq_client import GroqClient
from tg_voice_transcriber.llm_failover import FailoverChatClient
from tg_voice_transcriber.logging import configure_logging
from tg_voice_transcriber.openrouter_client import OpenRouterClient


# Test cases from the screenshots you showed
TEST_OFFERS = [
    {
        "text": "Блокируют интернет? Хотим напомнить что у нас есть быстрый и стабильный VPN "
        "с безлимитным трафиком по одной из самых выгодных цен — от 3₽ в сутки. "
        "А еще вы можете пользоваться им до 30 дней бесплатно! 🎉 "
        "Попробуй — @nnvpn_iobot",
        "expected_good": True,
        "expected_bot": "@nnvpn_iobot",
        "label": "NoName VPN 30 days free",
    },
    {
        "text": "VPN не работает? ❌ Попробуйте FadeVPN — 3 дня за 1₽ 🔥, Быстро и стабильно!",
        "expected_good": True,
        "expected_bot": "@fadevpnbot",  # Common pattern
        "label": "FadeVPN 3 days for 1 rub",
    },
    {
        "text": "Proxy #1: (самый быстрый) tg://proxy?server=s02.neo-trading.org&port=443&secret=eee6ec9f7e082baf...",
        "expected_good": False,  # Just a proxy string, not a trial offer
        "expected_bot": None,
        "label": "Raw proxy (should reject)",
    },
    {
        "text": "🎁 FREE VPN FOR 1 YEAR!!! Click here and enter your card details to activate!",
        "expected_good": False,  # Scam
        "expected_bot": None,
        "label": "Scam (card harvesting)",
    },
]


async def main() -> None:
    cfg = get_config()
    configure_logging("INFO")

    # Build LLM client (same failover setup as digest)
    groq = GroqClient(api_keys=cfg.groq_api_key.get_secret_value())
    groq.load()

    llm_client = groq
    if cfg.openrouter_api_keys:
        openrouter = OpenRouterClient(api_keys=cfg.openrouter_api_keys.get_secret_value())
        openrouter.load()
        llm_client = FailoverChatClient(
            primary=groq,
            fallback=openrouter,
            fallback_model=cfg.digest_fallback_model,
        )

    judge = OfferJudge(llm_client, model=cfg.digest_llm_model)

    print("Testing VPN offer judge with sample offers...\n")

    for i, test in enumerate(TEST_OFFERS, 1):
        print(f"Test {i}: {test['label']}")
        print(f"  Text: {test['text'][:80]}...")

        result = await judge.judge_offer(
            text=test["text"],
            channel_id=-1001234567890,  # fake
            message_id=None,
        )

        if result is None:
            print("  ✗ LLM failed to judge")
            continue

        print(f"  Good trial: {result.is_good_trial} (expected: {test['expected_good']})")
        print(f"  Days: {result.trial_days}, Price: {result.trial_price_rub}₽")
        print(f"  Scam: {result.scam_suspected}")
        print(f"  Target: {result.target_bot} (expected: {test.get('expected_bot', 'N/A')})")
        print(f"  Summary: {result.summary}")

        # Check expectations
        if result.is_good_trial != test["expected_good"]:
            print(f"  ⚠ is_good_trial mismatch!")
        if test.get("expected_bot") and result.target_bot != test["expected_bot"]:
            print(f"  ⚠ target_bot mismatch!")

        print()

    await groq.close()
    if isinstance(llm_client, FailoverChatClient) and llm_client._fallback:
        await llm_client._fallback.close()

    print("✓ Test complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
    except Exception as exc:
        print(f"\n✗ Test failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
