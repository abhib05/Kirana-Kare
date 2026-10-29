"""Basket engine behaviours beyond the golden conversations, plus the basket API."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.baskets.engine import BasketEngine
from app.customers.resolve import resolve_customer, strip_honorifics
from app.db.models import AuditLog, Basket, BasketItem, LedgerEntry, Product
from app.extraction.schemas import ExtractedItem, ExtractedTurn, ExtractionRejected, Turn
from app.ledger.service import customer_balance
from tests.conftest import SHOP_ID

T0 = datetime.now(UTC).replace(microsecond=0)


class ScriptedExtractor:
    """Returns pre-baked ExtractedTurns (or raises), standing in for the LLM."""

    prompt_version = "test"

    def __init__(self, outputs):
        self.outputs = list(outputs)

    def extract(self, turn, context, catalog):
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def item(hint, qty, unit=None, price=None):
    return ExtractedItem(raw_text=hint, product_hint=hint, qty=Decimal(qty), unit=unit,
                         spoken_price_paise=price)


def turn(i, text="..."):
    return Turn(f"t{i}", text, T0 + timedelta(seconds=10 * i), "A")


def test_invalid_llm_output_is_rejected_and_logged(db, redis_client):
    eng = BasketEngine(db, redis_client, SHOP_ID,
                       ScriptedExtractor([ExtractionRejected("schema validation failed", "{")]))
    res = eng.process_turn(turn(0, "do kilo atta"))
    assert res.status == "rejected"
    assert db.scalar(select(func.count()).select_from(Basket)) == 0
    log = db.execute(select(AuditLog).where(AuditLog.action == "extraction_rejected")
                     ).scalar_one()
    assert log.payload["text"] == "do kilo atta" and log.payload["raw"] == "{"


def test_one_bad_line_rejects_whole_turn(db, redis_client):
    out = ExtractedTurn(is_transactional=True, action="add_items",
                        items=[item("milk", 1), item("atta", 2, "kg", 99_000)])
    eng = BasketEngine(db, redis_client, SHOP_ID, ScriptedExtractor([out]))
    assert eng.process_turn(turn(0)).status == "rejected"
    assert db.scalar(select(func.count()).select_from(BasketItem)) == 0


def test_cash_intent_then_close(db, redis_client):
    outs = [
        ExtractedTurn(is_transactional=True, action="add_items", payment_mode="cash",
                      items=[item("onion", 1, "kg")]),
        ExtractedTurn(is_transactional=True, action="add_items", items=[item("potato", 2, "kg")]),
        ExtractedTurn(is_transactional=True, action="close_basket"),
    ]
    eng = BasketEngine(db, redis_client, SHOP_ID, ScriptedExtractor(outs))
    r1 = eng.process_turn(turn(0))
    assert r1.basket["status"] == "open" and "cash intent noted" in r1.actions
    eng.process_turn(turn(1))
    r3 = eng.process_turn(turn(2))
    assert r3.basket["status"] == "closed" and r3.basket["payment_mode"] == "cash"
    assert r3.basket["total_paise"] == 4000 + 7000


def test_unmapped_item_then_one_tap_map_adds_alias(client, db, redis_client):
    outs = [ExtractedTurn(is_transactional=True, action="add_items",
                          items=[item("kismis", 1, "packet")])]
    eng = BasketEngine(db, redis_client, SHOP_ID, ScriptedExtractor(outs))
    res = eng.process_turn(turn(0))
    line = res.basket["items"][0]
    assert line["product_id"] is None and line["flagged"] and res.basket["total_paise"] == 0

    biscuits = db.execute(select(Product).where(Product.name == "Parle-G Biscuits")).scalar_one()
    eng.map_item(line["id"], biscuits.id)
    db.expire_all()
    b = db.get(Basket, res.basket["id"])
    assert b.total_paise == 1000 and not b.items[0].flagged
    assert "kismis" in db.get(Product, biscuits.id).aliases


def test_udhaar_waits_for_customer_then_manual_close(client, db, redis_client):
    outs = [ExtractedTurn(is_transactional=True, action="close_basket", payment_mode="udhaar",
                          customer_name="Govind", items=[item("soap", 1)])]
    eng = BasketEngine(db, redis_client, SHOP_ID, ScriptedExtractor(outs))
    res = eng.process_turn(turn(0))
    assert res.basket["status"] == "awaiting_payment"
    assert res.needs_customer["spoken_name"] == "Govind"

    r = client.post(f"/api/baskets/{res.basket['id']}/close",
                    json={"mode": "udhaar", "customer_id": 4})
    assert r.status_code == 200 and r.json()["status"] == "closed"
    assert customer_balance(db, SHOP_ID, 4) == 3800
    # The spoken name is now an alias, so next time it resolves by voice.
    assert resolve_customer(db, SHOP_ID, "Govind").customer_id == 4


def test_text_turns_to_upi_alert_to_ledger(client, db):
    r = client.post("/api/dev/turn", json={"text": "do kilo atta aur ek doodh"})
    assert r.json()["basket"]["status"] == "open"
    r = client.post("/api/dev/turn", json={"text": "bas, phonepe se"})
    b = r.json()["basket"]
    assert b["status"] == "awaiting_payment" and b["total_paise"] == 12000

    alert = {"idempotency_key": "e2e-1", "amount_paise": 12000, "payer_name": "Someone",
             "ref_id": None, "received_at": datetime.now(UTC).isoformat()}
    res = client.post("/api/alerts", json=alert).json()
    assert res["alert"]["status"] == "matched"
    assert client.get(f"/api/baskets/{b['id']}").json()["status"] == "closed"
    assert db.execute(select(LedgerEntry.entry_type, LedgerEntry.amount_paise)).all() == [
        ("sale_upi", 12000)]


def test_alert_before_upi_statement_matches_when_basket_awaits(client, db):
    client.post("/api/dev/turn", json={"text": "ek parle g aur ek maggi"})
    alert = {"idempotency_key": "early-1", "amount_paise": 2400,
             "received_at": datetime.now(UTC).isoformat()}
    assert client.post("/api/alerts", json=alert).json()["alert"]["status"] in (
        "matched", "pending_confirm")
    client.post("/api/dev/turn", json={"text": "gpay kiya"})
    assert db.execute(select(LedgerEntry.amount_paise)).scalars().all() == [2400]


def test_manual_upi_close_cancel_and_lists(client, db):
    client.post("/api/dev/turn", json={"text": "ek chips"})
    open_b = client.get("/api/baskets?status=open").json()
    assert len(open_b) == 1
    r = client.post(f"/api/baskets/{open_b[0]['id']}/close", json={"mode": "upi"})
    assert r.json()["status"] == "awaiting_payment" and r.json()["upi_ref_id"]
    assert client.post(f"/api/baskets/{open_b[0]['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/baskets/{open_b[0]['id']}/close",
                       json={"mode": "cash"}).status_code == 409
    assert client.get("/api/baskets/00000000-0000-0000-0000-000000000000").status_code == 404


def test_sweep_idle(db, redis_client, settings):
    outs = [ExtractedTurn(is_transactional=True, action="add_items", items=[item("milk", 1)])]
    eng = BasketEngine(db, redis_client, SHOP_ID, ScriptedExtractor(outs))
    eng.process_turn(turn(0))
    assert eng.sweep_idle(T0 + timedelta(seconds=60)) == []
    swept = eng.sweep_idle(T0 + timedelta(seconds=settings.basket_idle_timeout_seconds + 60))
    assert [b.status for b in swept] == ["awaiting_payment"]
    assert eng.current_open() is None


def test_products_and_vendors_api(client):
    assert len(client.get("/api/products").json()) == 40
    v = client.post("/api/vendors", json={"name": "New Vendor", "phone": "919000000099"})
    assert v.status_code == 200
    p = client.post("/api/products", json={"name": "Kismis", "unit": "packet",
                                           "price_paise": 6000, "vendor_id": v.json()["id"]})
    assert p.json()["low_stock"] is True
    assert client.post("/api/vendors", json={"name": "x", "phone": "abc"}).status_code == 422


def test_customer_resolution(db):
    assert strip_honorifics("Ramesh Bhaiya") == "ramesh"
    assert resolve_customer(db, SHOP_ID, "ramesh bhaiya").customer_id == 1
    assert resolve_customer(db, SHOP_ID, "Suresh anna").customer_id == 2
    assert resolve_customer(db, SHOP_ID, "Venkat").customer_id == 6
    assert resolve_customer(db, SHOP_ID, "Govind").customer_id is None
    assert resolve_customer(db, SHOP_ID, None).customer_id is None
