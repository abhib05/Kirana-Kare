"""Reconciliation engine against Postgres: auto-match, confirm queue, merchant decisions,
and exactly-once under concurrent alerts."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db.models import AuditLog, Basket, CustomerAlias, LedgerEntry, Match, Reminder, UpiAlert
from app.ledger.service import Posting, customer_balance, post
from app.reconciliation.engine import reconcile_alert, rereconcile_pending
from tests.conftest import SHOP_ID

NOW = datetime.now(UTC).replace(microsecond=0)


def make_basket(db, total=15000, *, status="awaiting_payment", ref=None, customer_id=None,
                minutes_ago=0.0) -> Basket:
    t = NOW - timedelta(minutes=minutes_ago)
    b = Basket(id=uuid.uuid4(), shop_id=SHOP_ID, status=status, total_paise=total,
               upi_ref_id=ref, customer_id=customer_id, opened_at=t,
               awaiting_since=t if status == "awaiting_payment" else None,
               payment_mode="upi" if status == "awaiting_payment" else None)
    db.add(b)
    db.commit()
    return b


def alert_json(key, amount=15000, payer=None, ref=None, at=NOW):
    return {"idempotency_key": key, "amount_paise": amount, "payer_name": payer, "ref_id": ref,
            "received_at": at.isoformat(), "source": "simulated"}


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_auto_match_closes_basket_and_posts_sale(client, db):
    b = make_basket(db, ref="KKAAAAAA")
    r = client.post("/api/alerts", json=alert_json("a1", ref="KKAAAAAA"))
    assert r.json()["alert"]["status"] == "matched"
    assert r.json()["decision"]["outcome"] == "auto_match"

    db.expire_all()
    basket = db.get(Basket, b.id)
    assert (basket.status, basket.payment_mode) == ("closed", "upi")
    entry = db.execute(select(LedgerEntry).where(LedgerEntry.basket_id == b.id)).scalar_one()
    assert (entry.entry_type, entry.amount_paise) == ("sale_upi", 15000)
    m = db.execute(select(Match)).scalar_one()
    assert (m.tier, m.decided_by, m.kind) == (1, "auto", "sale")
    assert count(db, AuditLog, AuditLog.action == "reconciliation") == 1


def test_duplicate_alert_after_match_does_not_double_count(client, db):
    make_basket(db)
    client.post("/api/alerts", json=alert_json("dup"))
    r = client.post("/api/alerts", json=alert_json("dup"))
    assert r.json()["duplicate"] is True
    assert count(db, LedgerEntry) == 1
    assert count(db, Match) == 1


def test_ambiguous_goes_to_queue_then_merchant_picks(client, db):
    b1 = make_basket(db, customer_id=1)
    make_basket(db, minutes_ago=1)
    r = client.post("/api/alerts", json=alert_json("amb", payer="RAMESH KUMAR SHARMA"))
    alert = r.json()["alert"]
    assert alert["status"] == "pending_confirm"

    queue = client.get("/api/confirm-queue").json()
    assert len(queue) == 1 and len(queue[0]["candidates"]) == 2
    assert {c["basket_id"] for c in queue[0]["candidates"]} >= {str(b1.id)}

    d = client.post(f"/api/matches/{alert['id']}/decide",
                    json={"choice": "basket", "basket_id": str(b1.id)})
    assert d.status_code == 200 and d.json()["status"] == "matched"
    assert client.get("/api/confirm-queue").json() == []
    m = db.execute(select(Match)).scalar_one()
    assert m.decided_by == "merchant"
    # The confirmed payer name is learned as an alias for next time.
    assert count(db, CustomerAlias, CustomerAlias.alias == "RAMESH KUMAR SHARMA") == 1

    again = client.post(f"/api/matches/{alert['id']}/decide",
                        json={"choice": "not_a_sale"})
    assert again.status_code == 409


def test_merchant_marks_udhaar_repayment(client, db):
    post(db, Posting(SHOP_ID, "sale_udhaar", 50000, customer_id=2))
    db.commit()
    r = client.post("/api/alerts", json=alert_json("udh", amount=20000, payer="SURESH REDDY M"))
    body = r.json()
    assert body["alert"]["status"] == "pending_confirm"
    assert body["decision"]["suggested_customer_id"] == 2
    queue = client.get("/api/confirm-queue").json()
    assert queue[0]["suggested_customer"]["display_name"] == "Suresh Reddy"

    client.post(f"/api/matches/{body['alert']['id']}/decide",
                json={"choice": "udhaar_payment", "customer_id": 2})
    assert customer_balance(db, SHOP_ID, 2) == 30000


def test_not_a_sale(client, db):
    r = client.post("/api/alerts", json=alert_json("nas", amount=777))
    aid = r.json()["alert"]["id"]
    d = client.post(f"/api/matches/{aid}/decide", json={"choice": "not_a_sale"})
    assert d.json()["status"] == "ignored"
    assert count(db, LedgerEntry) == 0


def test_decide_validation(client, db):
    r = client.post("/api/alerts", json=alert_json("val", amount=555))
    aid = r.json()["alert"]["id"]
    assert client.post(f"/api/matches/{aid}/decide", json={"choice": "basket"}).status_code == 409
    assert client.post(f"/api/matches/{aid}/decide",
                       json={"choice": "basket", "basket_id": str(uuid.uuid4())}
                       ).status_code == 409
    assert client.post(f"/api/matches/{aid}/decide",
                       json={"choice": "udhaar_payment"}).status_code == 409
    assert client.post("/api/matches/99999/decide",
                       json={"choice": "not_a_sale"}).status_code == 409
    closed = make_basket(db, status="closed")
    assert client.post(f"/api/matches/{aid}/decide",
                       json={"choice": "basket", "basket_id": str(closed.id)}
                       ).status_code == 409
    # Nothing was half-applied by the failed attempts.
    assert count(db, LedgerEntry) == 0 and count(db, Match) == 0


def test_concurrent_alerts_for_one_basket_match_exactly_once(client, db):
    b = make_basket(db)
    alerts = [alert_json(f"race-{i}") for i in range(10)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda a: client.post("/api/alerts", json=a), alerts))
    assert all(r.status_code == 200 for r in results)
    statuses = [r.json()["alert"]["status"] for r in results]
    assert statuses.count("matched") == 1
    assert count(db, LedgerEntry, LedgerEntry.basket_id == b.id) == 1
    assert count(db, Match) == 1


def test_same_alert_reconciled_concurrently_matches_once(session_factory, db):
    make_basket(db)
    a = UpiAlert(shop_id=SHOP_ID, idempotency_key="conc", amount_paise=15000,
                 received_at=NOW, source="simulated", status="unmatched")
    db.add(a)
    db.commit()

    def run(_):
        with session_factory() as s:
            return reconcile_alert(s, SHOP_ID, a.id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(run, range(8)))
    assert sum(d is not None for d in decisions) == 1  # the rest saw it already matched
    assert count(db, Match) == 1
    assert count(db, LedgerEntry) == 1


def test_alert_before_basket_is_rematched(client, db):
    r = client.post("/api/alerts", json=alert_json("early", amount=4200))
    assert r.json()["alert"]["status"] == "pending_confirm"
    b = make_basket(db, total=4200)
    decisions = rereconcile_pending(db, SHOP_ID, b)
    assert [d.outcome for d in decisions] == ["auto_match"]
    db.expire_all()
    assert db.get(Basket, b.id).status == "closed"


def test_reminder_ref_pays_down_udhaar(client, db):
    post(db, Posting(SHOP_ID, "sale_udhaar", 60000, customer_id=3))
    db.add(Reminder(shop_id=SHOP_ID, customer_id=3, amount_paise=60000, ref_id="KKREMIND",
                    pay_link="upi://pay", message_text="pay", status="sent", due_at=NOW))
    db.commit()
    r = client.post("/api/alerts", json=alert_json("rem", amount=60000, ref="KKREMIND"))
    assert r.json()["alert"]["status"] == "matched"
    assert customer_balance(db, SHOP_ID, 3) == 0
    assert db.execute(select(Match)).scalar_one().kind == "udhaar_payment"
