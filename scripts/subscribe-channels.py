#!/usr/bin/env python3
"""Subscribe the FINDER account to discovered VPN channels — muted.

Runs on the finder session ONLY (cfg.finder_session_path = "rustam ibatulin"),
never the main userbot. Every channel is MUTED immediately after joining
(mandatory — no notification spam). Rate-limited to look human and avoid bans.

Candidate usernames come from either:
  - command-line args:  python scripts/subscribe-channels.py @a @b @c
  - the discovery log:  python scripts/subscribe-channels.py --from-log

By default this is a DRY RUN (prints what it would join). Pass --commit to
actually join + mute.

Usage:
    python scripts/subscribe-channels.py --from-log            # preview
    python scripts/subscribe-channels.py --from-log --commit   # do it
    python scripts/subscribe-channels.py @sotkavpn @mostvpn_free --commit
"""

from __future__ import annotations

import asyncio
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import TelegramClient
from telethon.tl.functions.channels import JoinChannelRequest

from tg_voice_transcriber.config import get_config
from tg_voice_transcriber.finder.mute import mute_peer

DISCOVER_LOG = Path(__file__).resolve().parent.parent / ".local" / "discover.log"

# Rate limits — joining many channels fast is a ban signal. Stay slow.
MIN_DELAY_S = 15.0
MAX_DELAY_S = 45.0
MAX_JOINS_PER_RUN = 20  # cap per invocation; run again later for more

# Parse "@username" tokens out of the discovery log's CHANNELS block.
_CHAN_LINE = re.compile(r"^\s+@(\w+)\s", re.MULTILINE)


def _load_from_log() -> list[str]:
    if not DISCOVER_LOG.exists():
        print(f"No discovery log at {DISCOVER_LOG} — run discover-channels.py first",
              file=sys.stderr)
        return []
    text = DISCOVER_LOG.read_text(encoding="utf-8", errors="replace")
    # Only take the CHANNELS section (between its header and the BOTS header).
    start = text.find("CHANNELS (")
    end = text.find("BOTS (")
    if start == -1:
        return []
    block = text[start:end if end != -1 else len(text)]
    return [m.group(1) for m in _CHAN_LINE.finditer(block)]


async def main() -> None:
    args = sys.argv[1:]
    commit = "--commit" in args
    from_log = "--from-log" in args
    explicit = [a.lstrip("@") for a in args if not a.startswith("--")]

    usernames = _load_from_log() if from_log else explicit
    if not usernames:
        print("No candidate usernames. Use --from-log or pass @names.", file=sys.stderr)
        sys.exit(1)

    # dedupe, preserve order
    seen: set[str] = set()
    usernames = [u for u in usernames if not (u.lower() in seen or seen.add(u.lower()))]

    cfg = get_config()
    client = TelegramClient(
        str(cfg.finder_session_path),
        cfg.api_id,
        cfg.api_hash.get_secret_value(),
    )
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT AUTHORIZED — run scripts/login-finder.py first", file=sys.stderr)
        sys.exit(1)

    me = await client.get_me()
    print(f"Finder account: {me.first_name} {me.last_name or ''}")
    print(f"{'COMMIT' if commit else 'DRY RUN'} — {len(usernames)} candidate channel(s)\n")

    # Skip ones we already follow.
    already: set[str] = set()
    async for dialog in client.iter_dialogs():
        u = getattr(dialog.entity, "username", None)
        if u:
            already.add(u.lower())

    todo = [u for u in usernames if u.lower() not in already]
    skipped = len(usernames) - len(todo)
    if skipped:
        print(f"({skipped} already followed — skipping)\n")

    if len(todo) > MAX_JOINS_PER_RUN:
        print(f"Capping to {MAX_JOINS_PER_RUN} joins this run "
              f"({len(todo) - MAX_JOINS_PER_RUN} left for next run).\n")
        todo = todo[:MAX_JOINS_PER_RUN]

    joined = 0
    for uname in todo:
        if not commit:
            print(f"  would join + mute  @{uname}")
            continue

        delay = random.uniform(MIN_DELAY_S, MAX_DELAY_S)
        await asyncio.sleep(delay)
        try:
            entity = await client.get_entity(uname)
            await client(JoinChannelRequest(entity))
            muted = await mute_peer(client, entity)
            joined += 1
            print(f"  joined {'+ muted' if muted else '(MUTE FAILED)'}  @{uname}")
        except Exception as exc:
            print(f"  FAILED  @{uname}: {exc}")

    if commit:
        print(f"\nJoined {joined}/{len(todo)} channels (all muted).")
    else:
        print(f"\nDry run — pass --commit to join these {len(todo)} channels.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
