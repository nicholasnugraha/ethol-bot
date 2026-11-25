from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: str = Field(..., min_length=10)
    allowed_chat_ids: str = Field(..., description="Comma-separated Telegram chat IDs")
    encryption_key: str = Field(..., min_length=32)

    novnc_public_url: str = (
        "http://localhost:6080/vnc.html?autoconnect=1&resize=scale&reconnect=1"
    )
    vnc_password: str | None = None

    ethol_base_url: str = "https://ethol.pens.ac.id"
    cas_login_url: str = (
        "https://login.pens.ac.id/cas/login"
        "?service=https%3A%2F%2Fethol.pens.ac.id%2Fapi%2Fauth%2Fcas-callback"
    )

    timezone: str = "Asia/Jakarta"
    poll_interval_seconds: int = Field(default=45, ge=15, le=300)
    window_before_minutes: int = Field(default=10, ge=0, le=60)
    window_after_minutes: int = Field(default=10, ge=0, le=60)
    attendance_mode: Literal["confirm", "auto"] = "confirm"
    daily_summary_time: str = "06:30"
    session_check_seconds: int = Field(default=600, ge=60)
    login_timeout_seconds: int = Field(default=300, ge=60, le=900)
    keep_browser_open: bool = False

    legacy_owner_id: int | None = None
    public_base_url: str = "https://localhost"
    gateway_host: str = "0.0.0.0"
    gateway_port: int = Field(default=8080, ge=1, le=65535)
    login_max_workers: int = Field(default=3, ge=1, le=32)
    ethol_max_concurrency: int = Field(default=8, ge=1, le=64)
    login_worker_image: str = "ethol-login-worker:latest"
    login_worker_network: str = "ethol-login"
    login_deployment_id: str = "ethol-bot"
    login_cookie_secure: bool = True

    db_path: str = "/data/app.db"
    browser_profile_dir: str = "/data/browser-profile"
    log_level: str = "INFO"

    @property
    def chat_ids(self) -> list[int]:
        ids: list[int] = []
        for part in self.allowed_chat_ids.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError as exc:
                raise ValueError(f"ALLOWED_CHAT_IDS bukan angka: {part!r}") from exc
        if not ids:
            raise ValueError("ALLOWED_CHAT_IDS minimal berisi satu chat ID")
        return ids

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin without path or credentials")
        return value.rstrip("/")

    @field_validator("legacy_owner_id")
    @classmethod
    def validate_owner(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("LEGACY_OWNER_ID must be a positive Telegram user ID")
        return value

    @field_validator("daily_summary_time")
    @classmethod
    def validate_daily_summary_time(cls, value: str) -> str:
        try:
            hour, minute = value.split(":")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError
        except Exception as exc:
            raise ValueError("DAILY_SUMMARY_TIME harus berformat HH:MM") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
