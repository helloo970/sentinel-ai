"""Configuration from environment variables and .env (prefix SENTINEL_)."""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1"}

DEFAULT_FEEDS: list[dict[str, str]] = [
    {"region": "de", "name": "tagesschau", "url": "https://www.tagesschau.de/index~rss2.xml"},
    {
        "region": "de",
        "name": "Deutschlandfunk",
        "url": "https://www.deutschlandfunk.de/nachrichten-100.rss",
    },
    {"region": "world", "name": "DW", "url": "https://rss.dw.com/xml/rss-en-all"},
    {"region": "world", "name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
]


DEFAULT_SHOPS: tuple[str, ...] = (
    "amazon.de",
    "otto.de",
    "mediamarkt.de",
    "saturn.de",
    "idealo.de",
    "galaxus.de",
    "alternate.de",
    "notebooksbilliger.de",
    "cyberport.de",
    "thomann.de",
    "conrad.de",
    "kaufland.de",
    "ikea.com",
    "decathlon.de",
)


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "SentinelAI"


def default_rescue_folders() -> list[Path]:
    home = Path.home()
    return [p for p in (home / "Documents", home / "Desktop") if p.is_dir()]


def default_brain_folders() -> list[Path]:
    home = Path.home()
    candidates = [
        home / "Documents",
        home / "Desktop",
        home / "OneDrive" / "Documents",
        home / "OneDrive" / "Dokumente",
        home / "OneDrive" / "Desktop",
        home / "SentinelAI",
    ]
    return [p for p in candidates if p.is_dir()]


def default_managed_folders() -> list[Path]:
    downloads = Path.home() / "Downloads"
    return [downloads] if downloads.is_dir() else []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        # Repo (development) and data folder (installed version); later files win
        env_file=(".env", "../.env", str(default_data_dir() / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Local API
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    api_token: SecretStr | None = None
    allowed_origins: list[str] = [
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
        "http://localhost:1420",
    ]
    data_dir: Path = Field(default_factory=default_data_dir)
    log_level: str = "INFO"

    # AI
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    llm_model: str = "claude-opus-5-5"
    # Google Gemini (if there is no Claude key). Best store the key via
    # "sentinel gemini login" in the Windows vault, alternatively GEMINI_API_KEY in .env
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "SENTINEL_GEMINI_API_KEY")
    )
    gemini_model: str = "gemini-flash-latest"
    # OpenRouter (takes precedence over Gemini). Store the key via "sentinel openrouter login"
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "SENTINEL_OPENROUTER_API_KEY"),
    )
    openrouter_model: str = Field(default="anthropic/claude-opus-5.5", max_length=100)
    # Extended thinking (otherwise as little as needed): more accurate, but slower
    openrouter_reasoning: bool = False
    llm_effort: str = "low"
    llm_fallbacks: bool = True
    budget_daily_eur: float = Field(default=2.0, ge=0)
    budget_monthly_eur: float = Field(default=30.0, ge=0)
    usd_to_eur: float = Field(default=0.92, gt=0)
    user_name: str = ""  # empty: the UI and Jarvis fall back to "Sir"

    # Autonomy and limits
    autonomy_default: int = Field(default=2, ge=0, le=3)
    max_autonomous_per_hour: int = Field(default=10, ge=1, le=100)
    undo_window_s: int = Field(default=60, ge=10, le=600)
    approval_ttl_s: int = Field(default=600, ge=30)

    # Folders for file actions (quarantine, cleanup, recycle bin) and monitoring
    managed_folders: list[Path] = Field(default_factory=default_managed_folders)
    watched_folders: list[Path] = Field(default_factory=default_managed_folders)
    monitor_interval_s: int = Field(default=30, ge=5)

    # Network
    home_subnet: str | None = None

    # Notifications (self-hosted ntfy on the home network)
    ntfy_url: str | None = None
    ntfy_topic: str | None = None
    ntfy_token: SecretStr | None = None

    # Server-Agent
    server_agent_url: str | None = None
    server_agent_token: SecretStr | None = None
    server_agent_ca: Path | None = None
    # Report status to the server for the phone view (emergency stop from the phone)
    server_relay: bool = True
    # Port of the phone view on the server agent (for the pairing link)
    phone_port: int = Field(default=8845, ge=1024, le=65535)
    # Backup watchdog: warn if the newest backup is older than this (hours)
    backup_max_age_h: int = Field(default=26, ge=1, le=24 * 30)

    # News
    news_feeds: list[dict[str, str]] = Field(default_factory=lambda: list(DEFAULT_FEEDS))

    # Speech
    voice_enabled: bool = False
    voice_wakeword: bool = True
    voice_clap: bool = True
    voice_clap_count: int = Field(default=2, ge=2, le=4)
    whisper_model: str = "small"
    # Speech recognition: ElevenLabs Scribe (online, accurate) with local Whisper as fallback
    stt_engine: str = Field(default="elevenlabs", pattern="^(elevenlabs|local)$")
    piper_voice: Path | None = None
    # Speech output: "edge" (Microsoft neural voice, online), "piper" (offline), "browser"
    tts_engine: str = Field(default="edge", pattern="^(edge|piper|browser)$")
    tts_voice: str = Field(default="en-US-AndrewNeural", max_length=80)

    # Weather, calendar, reports
    weather_enabled: bool = True
    home_city: str | None = None
    # ffmpeg for live camera video; empty = PATH or winget install
    ffmpeg_path: Path | None = None
    home_lat: float | None = Field(default=None, ge=-90, le=90)
    home_lon: float | None = Field(default=None, ge=-180, le=180)
    evening_report_time: str = Field(default="21:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    voice_followup_s: int = Field(default=30, ge=0, le=120)

    # Weekly cleanup of the approved folders (0 = Monday ... 6 = Sunday)
    downloads_cleanup: bool = True
    downloads_cleanup_day: int = Field(default=6, ge=0, le=6)
    downloads_cleanup_time: str = Field(default="10:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # File scanning: regularly load public malware hashes (abuse.ch)
    malware_feed: bool = True

    # Certificate watchdog: own services as "host:port" (RFC 1918 only)
    cert_watch: list[str] = Field(default_factory=list)

    # Order suggestions: only these shops (https)
    shop_domains: list[str] = Field(default_factory=lambda: list(DEFAULT_SHOPS))

    # Defense command rescue_files: these folders are copied on a critical alert
    rescue_folders: list[Path] = Field(default_factory=default_rescue_folders)
    rescue_max_mb: int = Field(default=500, ge=10, le=20_000)

    # Path of the Tauri app (set by the app itself; its autostart entry is not an alert)
    app_exe: str | None = None

    # Open web pages in an app window instead of the default browser
    web_in_app: bool = True

    # Quiet hours: Jarvis only speaks on critical alerts ("" = off)
    quiet_hours: str = Field(default="23:00-07:00", pattern=r"^$|^\d\d:\d\d-\d\d:\d\d$")

    # AI only in conversation: world situation, briefing, brain and agent schedule
    # without a language model
    ai_only_in_chat: bool = True

    # Brain: folders to ingest, AI requests per day, idle time, server backup
    brain_folders: list[Path] = Field(default_factory=default_brain_folders)
    brain_ai_daily: int = Field(default=15, ge=0, le=200)
    brain_idle_min: int = Field(default=5, ge=1, le=240)
    brain_backup_hours: int = Field(default=24, ge=0, le=24 * 14)

    # Agents
    agents_schedule_hours: int = Field(default=0, ge=0, le=168)

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        if value not in LOOPBACK_HOSTS:
            raise ValueError("backend must bind to 127.0.0.1 only")
        return value

    @field_validator("home_subnet")
    @classmethod
    def _private_subnet(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        from sentinel.permissions.netguard import validate_home_subnet

        return str(validate_home_subnet(value))

    @field_validator("ntfy_url", "server_agent_url")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        return value or None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sentinel.db"

    @property
    def config_dir(self) -> Path:
        return self.data_dir / "config"

    @property
    def quarantine_dir(self) -> Path:
        return self.data_dir / "quarantine"

    @property
    def plugins_dir(self) -> Path:
        return self.data_dir / "plugins"

    def subnet(self) -> ipaddress.IPv4Network | None:
        return ipaddress.IPv4Network(self.home_subnet) if self.home_subnet else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
