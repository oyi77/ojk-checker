from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- iDebKu endpoints ---
    ideb_base_url: HttpUrl = HttpUrl("https://idebku.ojk.go.id")  # type: ignore[call-arg]
    pre_register_url: HttpUrl = HttpUrl(
        "https://idebku.ojk.go.id/Public/PendaftaranOnline/PreRegister"
    )  # type: ignore[call-arg]
    status_url: HttpUrl = HttpUrl("https://idebku.ojk.go.id/Public/CekStatusLayanan")  # type: ignore[call-arg]
    captcha_url: HttpUrl = HttpUrl("https://idebku.ojk.go.id/get-captcha-image")  # type: ignore[call-arg]

    # --- Database ---
    db_path: Path = Path("data/slik.db")

    # --- Browser ---
    headless: bool = True
    browser_timeout: int = 30

    # --- HTTP ---
    request_timeout: int = 30
    max_retries: int = 3
    retry_backoff: float = 1.5

    # --- Captcha ---
    captcha_max_length: int = 6
    captcha_min_length: int = 4
    captcha_max_attempts: int = 3
    # External captcha service (e.g., 2Captcha)
    external_captcha_api_key: Optional[SecretStr] = None
    external_captcha_service_url: HttpUrl = HttpUrl("http://2captcha.com/in.php")  # type: ignore[call-arg]
    external_captcha_result_url: HttpUrl = HttpUrl("http://2captcha.com/res.php")  # type: ignore[call-arg]
    external_captcha_poll_interval: int = 5  # seconds
    external_captcha_timeout: int = 120  # seconds

    # --- Scheduler ---
    scheduler_timezone: str = "Asia/Jakarta"
    scheduler_check_interval: int = 60  # seconds, job checker

    # --- Notification ---
    telegram_bot_token: Optional[SecretStr] = None
    telegram_chat_id: Optional[str] = None
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[SecretStr] = None
    notify_email: Optional[str] = None

    # --- Logging ---
    log_level: str = "INFO"
    log_format: str = "json"  # json or console

    def ensure_data_dir(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
