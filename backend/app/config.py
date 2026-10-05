import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = os.getenv("KC_DB_PATH", "kirana.db")
    merchant_upi_id: str = os.getenv("KC_MERCHANT_UPI_ID", "kirana@upi")
    merchant_name: str = os.getenv("KC_MERCHANT_NAME", "Sharma General Store")
    match_window_s: int = int(os.getenv("KC_MATCH_WINDOW_SECONDS", "600"))
    auto_confirm: float = float(os.getenv("KC_AUTO_CONFIRM_THRESHOLD", "0.8"))
    sarvam_key: str = os.getenv("SARVAM_API_KEY", "")
    anthropic_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    wa_token: str = os.getenv("WHATSAPP_TOKEN", "")
    wa_phone_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    wa_merchant_phone: str = os.getenv("WHATSAPP_MERCHANT_PHONE", "")


def get_settings() -> Settings:
    return Settings()
