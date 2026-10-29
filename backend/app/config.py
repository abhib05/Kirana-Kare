"""Application settings, read from environment / .env (backend only)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    model_api_key: str = ""
    asr_model: str = "muse-voice-transcribe-1.0"
    asr_url: str = "wss://api.meta.ai/v1/asr/realtime"
    llm_model: str = "muse-spark-1.3"
    llm_base_url: str = "https://api.meta.ai/v1"

    database_url: str = "postgresql+psycopg://kk:kk@localhost:5433/kirana"
    redis_url: str = "redis://localhost:6380/0"

    demo_shop_id: int = 1
    shop_language_bias: str = "Hindi,English,Telugu"

    match_window_minutes: int = 10
    auto_match_threshold: float = 0.80
    basket_idle_timeout_seconds: int = 300

    udhaar_reminder_threshold_paise: int = 50_000
    udhaar_reminder_after_days: int = 7
    restock_multiplier: float = 2.0

    whatsapp_cloud_api_enabled: bool = False
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    scheduler_enabled: bool = True
    reminder_hour_ist: int = 10
    extractor_backend: str = Field(default="llm", pattern="^(llm|rules)$")

    @property
    def language_bias(self) -> list[str]:
        return [s.strip() for s in self.shop_language_bias.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
