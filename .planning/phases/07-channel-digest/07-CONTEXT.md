# Phase 7: Channel Digest (v1.1) - Context

**Gathered:** 2026-05-12
**Status:** Ready for planning
**Mode:** Smart discuss — decisions locked with user

<domain>
## Phase Boundary

Add an LLM-powered channel digest to the existing userbot. Listens for new posts in user-selected channels, buffers them, and every N minutes sends the batch to Groq's Llama LLM for relevance scoring against the user's stated preferences. Posts above threshold are formatted as a grouped-by-channel digest and delivered to a dedicated private channel.

Scope excludes:
- DM handling (already covered by voice-note feature)
- Non-text media in channels (audio/video-only posts — caption-only or skip for v1.1)
- Real-time per-message pings (by design — we batch)
- Groups or supergroups (channels only)
- Modifying existing voice-transcription behavior

</domain>

<decisions>
## Implementation Decisions

### Scope & Defaults
- On first `/digest setup`, ALL channels the user is subscribed to are added to the tracked list. User can `/digest unsub @channel` individually later.
- User's interest expressed as **free-form text prompt** (1-3 sentences, any language). Used verbatim as the system prompt for the LLM scorer.
- Default frequency: **every 30 minutes**.
- Delivery chat: **dedicated private channel the user creates**. On setup, user provides the `@channelname` or numeric chat_id of a channel where they've added themselves as admin. Bot verifies it can post there.
- Digest format: **grouped by channel**, with per-post summary + direct link to original.

### Scoring & Filtering
- LLM: **Groq `llama-3.3-70b-versatile`** (free tier, 1M tokens/day per key).
- Per-batch flow: collect posts → dedupe (near-duplicates across channels) → single LLM call with all posts + user prefs → JSON response with `[{post_id, score:1-10, summary}]` → filter to `score >= threshold` → format + send.
- Default threshold: **7** (only clearly relevant).
- Use the existing 6-key Groq rotation pool (GroqClient extracted as shared module).

### Deduplication
- Before scoring, compute a simple text-similarity hash on the first 200 chars of each post. Drop near-duplicates within the same batch (keep the first-arrived).
- Dedupe cache: last 24 hours of post hashes persisted in SQLite so cross-batch duplicates (e.g. news re-posted hours later by another channel) are also skipped.

### Persistence
- **SQLite at `/var/lib/tg-voice-transcriber/digest.db`** (tgbot:tgbot 0600).
- Tables:
  - `config` (single-row: delivery_chat_id, user_prefs_text, threshold, frequency_s, paused_bool)
  - `tracked_channels` (channel_id, channel_title, added_at)
  - `post_buffer` (channel_id, message_id, received_at, text) — cleared after each digest
  - `dedupe_cache` (post_hash, first_seen_at) — 24h TTL cleanup
  - `stats` (period_start, msgs_scanned, msgs_delivered, digest_sent_at) — for `/digest stats`

### Command Surface
All commands issued in Saved Messages (filter: outgoing message starting with `/digest` in self-chat):
- `/digest setup` — interactive wizard (chat w/ user via replies, persist config)
- `/digest pause` / `/digest resume`
- `/digest now` — flush current buffer immediately
- `/digest channels` — list tracked, with `+@name` / `-@name` shortcuts
- `/digest prefs` — show current prefs text; `/digest prefs <new text>` updates
- `/digest stats` — last 7 days: scanned vs delivered counts, noise ratio
- `/digest threshold <N>` — adjust score threshold (1-10)
- `/digest unsub @name` — shortcut for remove
- `/digest help` — list commands

### Ingest Architecture
- Add `ChatAction` / `NewMessage` listener for channels-only (`is_channel and not is_group`)
- Filter to channels in `tracked_channels` table
- Skip non-text posts (text length < 20 chars and no caption) — they rarely matter
- Write to `post_buffer` (fast, no LLM call)
- Background asyncio task scheduled every `frequency_s` processes the buffer

### LLM Prompt Shape
System: "You are a relevance filter for a personal Telegram digest. User's interests: <free-form text>. For each post, return JSON with score (1-10, 10=must-read) and one-line summary in user's language."
User: "Posts to score: [{id:1, channel:'@name', text:'...'}, ...]"
Response format: JSON mode. One LLM call per batch.

### No Back-fill
- First time a channel is tracked, mark `added_at = now()`. Only score posts with `received_at > added_at`.
- On service restart, do NOT re-score old buffered posts from before restart — drop buffer on clean shutdown.

### Empty Digest Handling
- If batch filters to 0 posts above threshold, log stats entry (msgs_delivered=0) but send NO message.
- Optional: weekly digest summary "this week we filtered 2,831 noise posts, delivered 47 relevant ones" — defer to later.

### Claude's Discretion
- Exact SQLite schema details (indexes, FK constraints)
- Whether to use aiosqlite async driver or run sqlite3 in executor
- LLM retry/backoff strategy if Groq returns non-JSON
- Whether to include channel username (`@name`) or title in groupings
- Exact wording of setup wizard prompts

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `groq_transcriber.GroqTranscriber` has the 6-key rotation and HTTP client — **extract a shared `groq_client.GroqClient`** class for both whisper and LLM calls to avoid duplication
- `config.Config` (pydantic-settings) — add new digest-related settings
- `client.TelegramUserbot` + `reply.ReplyService` — reusable for sending the digest message
- `queue.py` + `worker.py` patterns — similar pattern for the scheduled batch scorer
- `logging.py` privacy processor (hashed chat_ids) — use for digest logs

### Established Patterns
- asyncio event loop + single worker coroutine
- Telethon `events.NewMessage` handlers, filter inside handler
- Structured logging via structlog
- systemd service management, atomic commits per feature

### Integration Points
- Add new `events.NewMessage` handler with channel filter (in addition to existing voice-note handler)
- Add a `DigestScheduler` background task started in `__main__.py`
- Extend `__main__.py`'s graceful shutdown to drain the scheduler cleanly
- Add `digest_db_path` and digest-related fields to Config
- SQLite file in the same directory as the session file (simpler backups)

</code_context>

<specifics>
## Specific Ideas

- Bot detects if `/digest setup` is run without a delivery channel configured → walk user through "create a private channel, add yourself as admin, get the @username or chat link, paste it to me"
- Digest message format example:
  ```
  📋 Digest — 14:00 to 14:30  (3 of 47 relevant)

  🔬 @pythonweekly
  ▸ FastAPI 0.120 released — native ASGI lifespan improvements
    https://t.me/pythonweekly/1234

  💻 @aihub
  ▸ Anthropic ships Claude 4.8 with 2M context
    https://t.me/aihub/567
  ```
- Stats command output:
  ```
  📊 Digest stats — last 7 days
  Scanned: 4,231 posts
  Delivered: 108 (2.5%)
  Tracked channels: 47
  Top channels by delivered: @pythonweekly (23), @aihub (18), @oraclenews (12)
  ```

</specifics>

<deferred>
## Deferred Ideas

- Rich media handling (transcribe voice posts in channels) — separate feature
- Summary of the full digest at the top ("today's top 3 topics: X, Y, Z")
- Per-channel threshold override
- Keyword alerts (real-time ping regardless of batching)
- Learning from user reactions (👍/👎 to digest items changes future scoring) — next milestone
- Digest-to-Notion export — next milestone
- Multi-user support — out of project scope

</deferred>
