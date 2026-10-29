from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.db.models import UpiAlert

ALERT = {
    "idempotency_key": "sim-001",
    "amount_paise": 15000,
    "payer_name": "RAMESH K",
    "ref_id": "kkabcdef",
    "received_at": "2026-11-02T10:31:05+05:30",
    "source": "simulated",
}


def _count(db) -> int:
    return db.scalar(select(func.count()).select_from(UpiAlert))


def test_post_alert_then_duplicate_is_noop(client, db):
    first = client.post("/api/alerts", json=ALERT)
    assert first.status_code == 200
    body = first.json()
    assert body["duplicate"] is False
    assert body["alert"]["ref_id"] == "KKABCDEF"

    again = client.post("/api/alerts", json={**ALERT, "amount_paise": 99999})
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["alert"]["id"] == body["alert"]["id"]
    assert again.json()["alert"]["amount_paise"] == 15000  # original result, not the replay
    assert _count(db) == 1


def test_concurrent_duplicate_deliveries_insert_once(client, db):
    alert = {**ALERT, "idempotency_key": "sim-race"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.post("/api/alerts", json=alert), range(16)))
    assert all(r.status_code == 200 for r in results)
    assert sum(not r.json()["duplicate"] for r in results) == 1
    assert len({r.json()["alert"]["id"] for r in results}) == 1
    assert _count(db) == 1


def test_alert_validation(client):
    assert client.post("/api/alerts", json={**ALERT, "amount_paise": 0}).status_code == 422
    assert client.post("/api/alerts", json={**ALERT, "amount_paise": 150.5}).status_code == 422
    naive = {**ALERT, "received_at": "2026-11-02T10:31:05"}
    assert client.post("/api/alerts", json=naive).status_code == 422
    assert client.post("/api/alerts", json={**ALERT, "source": "carrier pigeon"}).status_code == 422


def test_parse_sms_endpoint_and_replay(client, db):
    text = ("Rs.150.00 credited to a/c XXXXXX1234 on 02-11-26 by a/c linked to VPA "
            "ramesh.k@okicici (UPI Ref No 430612345678).")
    r1 = client.post("/api/alerts/parse-sms", json={"text": text})
    assert r1.status_code == 200
    assert r1.json()["alert"]["amount_paise"] == 15000
    assert r1.json()["alert"]["source"] == "sms_paste"
    r2 = client.post("/api/alerts/parse-sms", json={"text": text})
    assert r2.json()["duplicate"] is True
    assert _count(db) == 1


def test_parse_sms_rejects_debit(client):
    r = client.post("/api/alerts/parse-sms",
                    json={"text": "Rs.500.00 debited from a/c XX1234 UPI Ref 430612345699"})
    assert r.status_code == 422


def test_udhaar_payment_and_reversal_api(client):
    r = client.post("/api/udhaar/1/payment", json={"amount_paise": 5000})
    assert r.status_code == 200
    entry_id = r.json()["id"]
    balances = {b["customer_id"]: b["balance_paise"] for b in client.get("/api/udhaar").json()}
    assert balances[1] == -5000
    assert client.post(f"/api/ledger/{entry_id}/reverse", json={}).status_code == 200
    assert client.post(f"/api/ledger/{entry_id}/reverse", json={}).status_code == 409
    balances = {b["customer_id"]: b["balance_paise"] for b in client.get("/api/udhaar").json()}
    assert balances[1] == 0
    assert client.post("/api/udhaar/999/payment", json={"amount_paise": 5}).status_code == 404
