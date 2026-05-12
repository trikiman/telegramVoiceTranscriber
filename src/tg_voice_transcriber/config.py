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
        env_prefix="TG_VOICE_",
        extra="ignore",
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

    # Logging level (DEBUG / INFO / WARNING / ERROR)
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Return the singleton Config instance (cached after first call)."""
    return Config()  # type: ignore[call-arg]
