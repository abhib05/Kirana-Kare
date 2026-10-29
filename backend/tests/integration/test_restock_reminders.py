"""Phase 7 acceptance: selling past the reorder level drafts a vendor message with a
working wa.me link; the reminder job drafts correct udhaar reminders with pay links."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import select

from app.db.models import Product, Reminder, RestockDraft
from app.ledger.service import Posting, customer_balance, post
from app.reminders.jobs import run_udhaar_reminders
from tests.conftest import SHOP_ID


def stock(db, name) -> Decimal:
    db.expire_all()
    return db.execute(select(Product.stock_qty).where(Product.name == name)).scalar_one()


def sell(client, text, pay="bas cash"):
    client.post("/api/dev/turn", json={"text": text})
    return client.post("/api/dev/turn", json={"text": pay}).json()


def test_cash_sale_decrements_stock(client, db):
    sell(client, "do kilo atta aur teen maggi")
    assert stock(db, "Atta") == Decimal("38")
    assert stock(db, "Maggi Noodles") == Decimal("37")


def test_upi_match_decrements_stock(client, db):
    r = sell(client, "ek kilo cheeni", pay="gpay")
    assert stock(db, "Sugar") == Decimal("40")  # not yet paid
    client.post("/api/alerts", json={"idempotency_key": "inv-1", "amount_paise": 4500,
                                     "ref_id": r["basket"]["upi_ref_id"],
                                     "received_at": datetime.now(UTC).isoformat()})
    assert stock(db, "Sugar") == Decimal("39")


def test_unconvertible_unit_does_not_touch_stock(client, db):
    client.post("/api/dev/turn", json={"text": "500 gram jeera"})
    items = client.get("/api/basket-items/unmapped").json()
    client.post(f"/api/baskets/{items[0]['basket_id']}/close", json={"mode": "cash"})
    assert stock(db, "Jeera") == Decimal("12")


def test_selling_past_reorder_level_creates_vendor_draft(client, db):
    sell(client, "31 kilo atta")  # 40 -> 9, reorder level 10
    drafts = client.get("/api/restock-drafts").json()
    assert len(drafts) == 1
    d = drafts[0]
    assert d["vendor_name"] == "Sharma Wholesale Grains"
    assert d["items"] == [{"product_id": 1, "name": "Atta", "unit": "kg", "qty": "11",
                           "stock": "9", "edited": False}]  # 2 x 10 - 9
    link = urlparse(d["wa_link"])
    assert (link.scheme, link.netloc, link.path) == ("https", "wa.me", "/919000000001")
    text = unquote(parse_qs(link.query)["text"][0])
    assert text == d["message_text"] and "- Atta: 11 kg" in text and "Sri Lakshmi Kirana" in text


def test_draft_is_updated_not_duplicated_and_edits_survive(client, db):
    sell(client, "31 kilo atta")
    d = client.get("/api/restock-drafts").json()[0]
    r = client.patch(f"/api/restock-drafts/{d['id']}", json={"quantities": {"1": "25"}})
    assert "- Atta: 25 kg" in r.json()["message_text"]

    sell(client, "14 kilo chawal")  # rice 60 -> 46, not low
    sell(client, "12 kilo moong dal")  # 15 -> 3, reorder 4: same vendor
    drafts = client.get("/api/restock-drafts").json()
    assert len(drafts) == 1
    items = {i["name"]: i["qty"] for i in drafts[0]["items"]}
    assert items == {"Atta": "25", "Moong Dal": "5"}  # merchant's 25 kept; 2x4-3 = 5


def test_sent_items_are_on_order_then_new_low_items_get_new_draft(client, db):
    sell(client, "31 kilo atta")
    d = client.get("/api/restock-drafts").json()[0]
    assert client.post(f"/api/restock-drafts/{d['id']}/sent").json()["status"] == "sent"
    assert client.post(f"/api/restock-drafts/{d['id']}/sent").status_code == 409

    sell(client, "ek kilo atta")  # still low, but already ordered
    assert client.get("/api/restock-drafts").json() == []
    sell(client, "12 kilo moong dal")
    drafts = client.get("/api/restock-drafts").json()
    assert [i["name"] for i in drafts[0]["items"]] == ["Moong Dal"]


def test_dismiss_edit_validation_and_receive(client, db):
    sell(client, "31 kilo atta")
    d = client.get("/api/restock-drafts").json()[0]
    assert client.patch(f"/api/restock-drafts/{d['id']}",
                        json={"quantities": {"1": "0"}}).status_code == 409  # empty draft
    assert client.patch(f"/api/restock-drafts/{d['id']}",
                        json={"quantities": {"1": "-1"}}).status_code == 409
    assert client.post(f"/api/restock-drafts/{d['id']}/dismiss").json()["status"] == "dismissed"
    r = client.post("/api/products/1/receive", json={"qty": "30"})
    assert r.json()["stock_qty"] == "39" and r.json()["low_stock"] is False
    assert client.post("/api/products/999/receive", json={"qty": "1"}).status_code == 404


def _owe(db, customer_id, paise):
    post(db, Posting(SHOP_ID, "sale_udhaar", paise, customer_id=customer_id))
    db.commit()


def test_reminder_job_drafts_correct_reminders(db):
    _owe(db, 1, 80_000)  # Ramesh, has phone
    _owe(db, 4, 60_000)  # Priya, no phone
    _owe(db, 2, 20_000)  # Suresh, below threshold
    later = datetime.now(UTC) + timedelta(days=8)

    assert run_udhaar_reminders(db, SHOP_ID, now=datetime.now(UTC)) == []  # too soon
    made = run_udhaar_reminders(db, SHOP_ID, now=later)
    by_customer = {r.customer_id: r for r in made}
    assert set(by_customer) == {1, 4}

    r = by_customer[1]
    assert r.amount_paise == 80_000 and r.status == "draft"
    q = {k: v[0] for k, v in parse_qs(urlparse(r.pay_link).query).items()}
    assert (q["am"], q["tr"], q["pa"]) == ("800.00", r.ref_id, "srilakshmikirana@okaxis")
    assert r.wa_link.startswith("https://wa.me/919100000001?text=")
    assert "₹800" in r.message_text
    assert by_customer[4].wa_link is None

    assert run_udhaar_reminders(db, SHOP_ID, now=later) == []  # no duplicates


def test_recent_payment_defers_reminder(db):
    _owe(db, 1, 80_000)
    post(db, Posting(SHOP_ID, "udhaar_payment", 10_000, customer_id=1))
    db.commit()
    assert run_udhaar_reminders(db, SHOP_ID, now=datetime.now(UTC) + timedelta(days=3)) == []
    assert len(run_udhaar_reminders(db, SHOP_ID,
                                    now=datetime.now(UTC) + timedelta(days=8))) == 1


def test_paying_through_reminder_link_clears_balance(client, db):
    _owe(db, 3, 75_000)
    r = client.post("/api/udhaar/3/reminder").json()
    assert r["status"] == "draft" and r["customer_name"] == "Lakshmi Devi"
    alert = {"idempotency_key": "rem-pay", "amount_paise": 75_000, "ref_id": r["ref_id"],
             "payer_name": "LAKSHMI D", "received_at": datetime.now(UTC).isoformat()}
    assert client.post("/api/alerts", json=alert).json()["alert"]["status"] == "matched"
    assert customer_balance(db, SHOP_ID, 3) == 0


def test_reminder_api(client, db):
    assert client.post("/api/udhaar/5/reminder").status_code == 409  # owes nothing
    _owe(db, 5, 70_000)
    first = client.post("/api/udhaar/5/reminder").json()
    second = client.post("/api/udhaar/5/reminder").json()
    listed = client.get("/api/reminders").json()
    assert [x["id"] for x in listed] == [second["id"]]  # the first was superseded
    assert db.get(Reminder, first["id"]).status == "dismissed"
    assert client.post(f"/api/reminders/{second['id']}/sent").json()["status"] == "sent"
    assert client.post(f"/api/reminders/{second['id']}/sent").status_code == 409
    assert client.post(f"/api/reminders/{second['id']}/nope").status_code == 404
    assert client.post("/api/reminders/run").json() == {"created": 0}
    assert db.execute(select(RestockDraft)).first() is None
