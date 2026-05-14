# Phase 2: Audio Pipeline - Context

**Gathered:** 2026-05-12
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Given raw Telegram voice-note bytes (OGG/Opus), produce 16 kHz mono signed-16-bit PCM suitable for faster-whisper. Includes duration guards (reject <1s and >10min), fail-fast FFmpeg check at startup, and clean resource handling (no temp file leaks). Pure byte-in / byte-out — no Telegram dependency.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Key constraints from research:
- Use FFmpeg subprocess (not pydub, not ffmpeg-python) — pipe via stdin/stdout for voice notes <1 MB
- Output format: 16 kHz, mono, signed 16-bit little-endian PCM (raw, no WAV header) — faster-whisper accepts this directly
- Duration extracted from OGG metadata via `ffprobe` or from Telegram's `DocumentAttributeAudio.duration` field (caller provides it)
- Configurable min/max duration thresholds via Config (add fields)
- Fail-fast: check `ffmpeg -version` at module import or startup; raise clear error if missing

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Config` class from Phase 1 — extend with `min_voice_duration_s` and `max_voice_duration_s` fields

### Established Patterns
- src-layout, structlog logging, pydantic-settings config

### Integration Points
- Phase 3 (Transcriber) will call `AudioPipeline.convert(ogg_bytes, duration_s) -> pcm_bytes`
- Phase 4 (Worker) will call the pipeline after downloading voice bytes from Telegram

</code_context>

<specifics>
## Specific Ideas

- `scripts/smoke.py` that takes a local .ogg file path, runs it through the pipeline, prints byte count and first few sample values — proves the pipeline works without Telegram
- A test fixture `.ogg` file (can be generated with ffmpeg from silence or a tone)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>
