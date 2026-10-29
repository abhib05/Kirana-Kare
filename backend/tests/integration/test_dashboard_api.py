from datetime import UTC, datetime

from app.alerts.sms_parser import IST


def _alert(key, amount, payer=None):
    return {"idempotency_key": key, "amount_paise": amount, "payer_name": payer,
            "received_at": datetime.now(UTC).isoformat()}


def test_summary_counts_modes_and_alerts(client):
    client.post("/api/dev/turn", json={"text": "ek parle g, bas cash"})
    client.post("/api/dev/turn", json={"text": "do maggi"})
    client.post("/api/dev/turn", json={"text": "gpay"})
    client.post("/api/alerts", json=_alert("s1", 2800))  # auto-matches the maggi basket
    client.post("/api/alerts", json=_alert("s2", 999))  # nothing to match
    client.post("/api/dev/turn", json={"text": "ek dahi"})
    client.post("/api/dev/turn", json={"text": "Ramesh bhaiya ke khate mein likh do"})

    s = client.get("/api/summary").json()
    assert s["date"] == datetime.now(IST).date().isoformat()
    assert (s["cash_paise"], s["upi_paise"], s["udhaar_paise"]) == (1000, 2800, 3500)
    assert s["total_sales_paise"] == 7300
    assert s["alerts"] == {"matched": 1, "pending": 1, "ignored": 0, "auto_matched": 1}
    assert s["baskets"]["closed"] == 3

    empty = client.get("/api/summary?date=2020-01-01").json()
    assert empty["total_sales_paise"] == 0 and empty["alerts"]["matched"] == 0


def test_unmapped_items_listed_until_mapped(client):
    client.post("/api/dev/turn", json={"text": "500 gram jeera"})  # g vs per-packet SKU
    items = client.get("/api/basket-items/unmapped").json()
    assert len(items) == 1 and items[0]["raw_text"] == "500 gram jeera"
    products = {p["name"]: p["id"] for p in client.get("/api/products").json()}
    client.post(f"/api/basket-items/{items[0]['id']}/map", json={"product_id": products["Jeera"]})
    assert client.get("/api/basket-items/unmapped").json() == []
