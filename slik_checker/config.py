from __future__ import annotations

import logging
from pathlib import Path

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
    register_url: HttpUrl = HttpUrl(
        "https://idebku.ojk.go.id/Public/PendaftaranOnline/Register"
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
    quota_retry_delay: float = 2.0
    quota_max_retries: int = 5
    # --- Captcha ---
    # captcha_mode: "auto"   -> vision-LLM solver (if enabled), else external service
    #                           (if key set), else local OCR
    #               "manual" -> save the captcha image and prompt the operator to type it
    #                           (100% free, no signup, for environments where OCR fails)
    #               "vision" -> force the vision-LLM solver (requires vision_captcha_enabled)
    captcha_mode: str = "auto"
    captcha_max_length: int = 6
    # Operator-provided captcha text (e.g. read from the saved image by a human or
    # an external vision model). When set, _solve_captcha returns it directly
    # WITHOUT fetching a fresh captcha — so the submitted value matches the captcha
    # already issued in the session. Free, no OCR / no external service.
    captcha_override: str | None = None
    captcha_min_length: int = 4
    captcha_max_attempts: int = 5
    # External captcha service. 2Captcha-compatible endpoints are used; point
    # `external_captcha_service_url`/`result_url` at any compatible provider.
    # Free alternative with no payment: 9kw.eu (earn credits by solving) —
    # set service_url="https://www.9kw.eu/index.cgi" and result_url likewise.
    external_captcha_api_key: SecretStr | None = None
    # Vision-LLM captcha solver — FREE with a free-tier API key (no payment):
    #   • Gemini 2.5 Flash free tier (default below) — get a free key at
    #     https://aistudio.google.com/apikey ; works via Gemini's OpenAI-compatible
    #     endpoint.  • GLM-4.6v free quota (Zhipu AI).  • Local Ollama vision model
    #     (http://localhost:11434/v1, no key). Pattern from epic-freebies-helper.
    vision_captcha_enabled: bool = False
    vision_captcha_api_base: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    vision_captcha_api_key: SecretStr | None = None
    vision_captcha_model: str = "gemini-2.5-flash"
    vision_captcha_prompt: str = (
        "This is a CAPTCHA image containing distorted text characters. "
        "Transcribe the exact characters shown, in order, with no spaces or "
        "punctuation. Return only the characters."
    )
    # --- Pose / Image Generation AI (OmniRoute / Agnes / OpenAI Gateway / Gemini) ---
    pose_ai_api_base: str | None = None
    pose_ai_api_key: SecretStr | None = None
    pose_ai_model: str = "nano-banana-pro-preview"
    external_captcha_service_url: HttpUrl = HttpUrl("http://2captcha.com/in.php")  # type: ignore[call-arg]
    external_captcha_result_url: HttpUrl = HttpUrl("http://2captcha.com/res.php")  # type: ignore[call-arg]
    external_captcha_poll_interval: int = 5  # seconds
    external_captcha_timeout: int = 120  # seconds
    # --- Scheduler ---
    scheduler_timezone: str = "Asia/Jakarta"
    scheduler_check_interval: int = 60  # seconds, job checker

    # --- Notification ---
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    notify_email: str | None = None

    # --- Logging ---
    log_level: str = "INFO"
    log_format: str = "json"  # json or console

    def ensure_data_dir(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
