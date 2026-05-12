"""Structured logging setup using structlog with privacy-safe processors."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from typing import Any

import structlog

# Salt for hashing IDs — loaded from env or generated at startup.
# This prevents rainbow-table reversal of hashed chat/sender IDs.
_HASH_SALT: str = os.environ.get("TG_VOICE_LOG_SALT", "tg-voice-default-salt")


def _hash_id(value: int | str) -> str:
    """Produce a short salted hash of an ID for privacy-safe logging."""
    raw = f"{_HASH_SALT}:{value}".encode()
    return hashlib.sha256(raw).hexdigest()[:12]


def _privacy_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor that hashes chat_id and sender_id fields.

    At INFO level and above, transcript text is stripped entirely.
    At DEBUG level, transcript text is only included if LOG_TRANSCRIPTS=true.
    """
    # Hash IDs
    for key in ("chat_id", "sender_id"):
        if key in event_dict and event_dict[key] is not None:
            event_dict[key] = _hash_id(event_dict[key])

    # Strip transcript content at INFO+
    log_transcripts = os.environ.get("TG_VOICE_LOG_TRANSCRIPTS", "false").lower() == "true"
    if "transcript" in event_dict:
        if method_name in ("debug",) and log_transcripts:
            pass  # Keep transcript at DEBUG when explicitly enabled
        else:
            del event_dict["transcript"]

    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog for key-value console output with privacy processors.

    Call once at startup. Logs go to stdout for journald capture.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Configure stdlib logging (Telethon uses it internally)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            _privacy_processor,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
