"""WhatsApp messaging. Default: `wa.me` deep links the merchant taps (WhatsApp opens
pre-filled, one more tap sends; no Business verification needed). Optional: the WhatsApp
Business Cloud API, behind WHATSAPP_CLOUD_API_ENABLED. The system never messages customers
on its own in the MVP."""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from app.config import Settings

GRAPH_URL = "https://graph.facebook.com/v20.0/{phone_number_id}/messages"


class WhatsAppError(RuntimeError):
    pass


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:  # bare Indian mobile number
        digits = "91" + digits
    if not 11 <= len(digits) <= 15:
        raise WhatsAppError(f"not a valid WhatsApp number: {phone!r}")
    return digits


def wa_link(phone: str, text: str) -> str:
    return f"https://wa.me/{normalize_phone(phone)}?text={quote(text, safe='')}"


def send_via_cloud_api(settings: Settings, phone: str, text: str,
                       client: httpx.Client | None = None) -> str:
    """Send a text message through the Cloud API. Returns the message id."""
    if not settings.whatsapp_cloud_api_enabled:
        raise WhatsAppError("WhatsApp Cloud API is disabled (WHATSAPP_CLOUD_API_ENABLED=false)")
    if not settings.whatsapp_token or not settings.whatsapp_phone_number_id:
        raise WhatsAppError("WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID must be set")
    http = client or httpx.Client(timeout=10)
    resp = http.post(
        GRAPH_URL.format(phone_number_id=settings.whatsapp_phone_number_id),
        headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
        json={"messaging_product": "whatsapp", "to": normalize_phone(phone), "type": "text",
              "text": {"body": text}},
    )
    if resp.status_code >= 400:
        raise WhatsAppError(f"Cloud API error {resp.status_code}: {resp.text[:200]}")
    return str(resp.json()["messages"][0]["id"])
