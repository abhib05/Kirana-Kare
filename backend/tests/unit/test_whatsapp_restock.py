import json
from decimal import Decimal

import httpx
import pytest

from app.notify.whatsapp import WhatsAppError, normalize_phone, send_via_cloud_api, wa_link
from app.restock.drafts import compose_message, suggested_qty


@pytest.mark.parametrize(("raw", "out"), [("919000000001", "919000000001"),
                                          ("9000000001", "919000000001"),
                                          ("+91 90000-00001", "919000000001")])
def test_normalize_phone(raw, out):
    assert normalize_phone(raw) == out


@pytest.mark.parametrize("bad", ["", "12345", "abc", "1" * 16])
def test_normalize_phone_rejects(bad):
    with pytest.raises(WhatsAppError):
        normalize_phone(bad)


def test_wa_link_encodes_everything():
    link = wa_link("9000000001", "Atta: 11 kg & more\nDhanyavaad!")
    assert link == "https://wa.me/919000000001?text=Atta%3A%2011%20kg%20%26%20more%0ADhanyavaad%21"


@pytest.mark.parametrize(("stock", "reorder", "mult", "out"),
                         [("9", "10", 2.0, "11"), ("3", "4", 2.0, "5"), ("0", "10", 2.0, "20"),
                          ("19.5", "10", 2.0, "1"), ("2.2", "3", 2.0, "4"), ("25", "10", 2.0, "1")])
def test_suggested_qty(stock, reorder, mult, out):
    assert suggested_qty(Decimal(stock), Decimal(reorder), mult) == Decimal(out)


def test_compose_message():
    msg = compose_message("Shop", "Vendor", [{"name": "Atta", "qty": "11", "unit": "kg"}])
    assert msg.startswith("Namaste Vendor ji,") and "- Atta: 11 kg" in msg


def test_cloud_api_disabled_by_default(settings):
    with pytest.raises(WhatsAppError, match="disabled"):
        send_via_cloud_api(settings, "9000000001", "hi")


def test_cloud_api_sends(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    s = settings.model_copy(update={"whatsapp_cloud_api_enabled": True, "whatsapp_token": "t",
                                    "whatsapp_phone_number_id": "123"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_via_cloud_api(s, "9000000001", "hello", client) == "wamid.1"
    assert seen["url"].endswith("/123/messages") and seen["auth"] == "Bearer t"
    assert seen["body"]["to"] == "919000000001" and seen["body"]["text"] == {"body": "hello"}

    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    with pytest.raises(WhatsAppError, match="401"):
        send_via_cloud_api(s, "9000000001", "hello", bad)
