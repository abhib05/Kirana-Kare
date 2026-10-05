"""External services: Sarvam STT and WhatsApp Cloud API. Both degrade gracefully without keys."""
import httpx

from .config import get_settings


def transcribe(audio: bytes, filename: str, content_type: str, language: str = "unknown") -> str:
    """Sarvam AI Indic speech-to-text. Audio is only held in memory and never written to disk."""
    s = get_settings()
    if not s.sarvam_key:
        raise RuntimeError("SARVAM_API_KEY not set; use the browser mic or type the utterance instead")
    code = {"hi": "hi-IN", "kn": "kn-IN", "te": "te-IN", "en": "en-IN"}.get(language, "unknown")
    r = httpx.post(
        "https://api.sarvam.ai/speech-to-text",
        headers={"api-subscription-key": s.sarvam_key},
        files={"file": (filename, audio, content_type)},
        data={"model": "saarika:v2.5", "language_code": code},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("transcript", "")


def send_whatsapp(phone: str, body: str) -> bool:
    s = get_settings()
    if not (s.wa_token and s.wa_phone_id and phone):
        return False
    try:
        r = httpx.post(
            f"https://graph.facebook.com/v20.0/{s.wa_phone_id}/messages",
            headers={"Authorization": f"Bearer {s.wa_token}"},
            json={"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": body}},
            timeout=10,
        )
        return r.status_code < 300
    except httpx.HTTPError:
        return False
