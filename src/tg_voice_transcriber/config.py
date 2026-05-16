"""Typed configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Application configuration.

    All values are loaded from environment variables prefixed with ``TG_VOICE_``.
    In development, a ``.env`` file in the project root is loaded automatically
    via python-dotenv (if present).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TG_VOICE_",
        extra="ignore",
        # In production, secrets come from systemd EnvironmentFile (real env vars).
        # .env is a dev-only convenience. Don't fail if it's missing or unreadable.
    )

    # Telegram API credentials (from https://my.telegram.org)
    api_id: int
    api_hash: SecretStr

    # Phone number in E.164 format (e.g. +79161234567)
    phone: str

    # Path to the Telethon SQLite session file.
    # Dev default: .local/userbot.session (gitignored).
    # Production: /var/lib/tg-voice-transcriber/userbot.session (via systemd EnvironmentFile).
    session_path: Path = Path(".local/userbot.session")

    # Transcription backend
    use_groq: bool = False  # If True, use Groq API; if False, use local faster-whisper
    groq_api_key: SecretStr | None = None
    groq_model: str = "whisper-large-v3-turbo"
    default_language: str = "ru"  # ISO 639-1 — "ru" or "en" or "" for auto-detect

    # Channel digest (v1.1)
    digest_enabled: bool = True  # master switch — actual "ready" depends on DB config
    digest_db_path: Path = Path(".local/digest.db")  # overridden by systemd env in prod
    digest_llm_model: str = "llama-3.3-70b-versatile"
    digest_default_track_all: bool = True  # auto-add unknown channels to tracked list

    # OpenRouter fallback (v1.2 — phase 8.1)
    # If set, the digest scorer will fall back to OpenRouter when Groq
    # raises any exception (rate-limited, all keys exhausted, network, etc.).
    # Leave empty to disable failover and use Groq only.
    openrouter_api_keys: SecretStr | None = None  # comma-separated, one or more
    # Default DeepSeek-V4-Flash :free — fast, 1M context, less upstream-throttled
    # than Llama-3.3 free pool. Override via env if needed.
    digest_fallback_model: str = "deepseek/deepseek-v4-flash:free"

    # Audio pipeline settings
    min_voice_duration_s: float = 1.0  # Skip voice notes shorter than this (whisper hallucinates)
    max_voice_duration_s: float = 600.0  # 10 minutes — reject longer (CPU hog)

    # Queue settings
    queue_maxsize: int = 10  # Bounded queue — excess jobs are dropped

    # Worker settings
    grace_period_s: float = 10.0  # Seconds to wait for in-flight job on shutdown

    # Privacy / logging
    log_level: str = "INFO"
    log_transcripts: bool = False  # If True, transcript text is logged at DEBUG level


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Return the singleton Config instance (cached after first call)."""
    return Config()  # type: ignore[call-arg]
