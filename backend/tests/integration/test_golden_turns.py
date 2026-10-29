"""Phase 4 acceptance: golden typed turns produce the right baskets and ledger entries."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.baskets.engine import BasketEngine
from app.db.models import AuditLog, Basket, Customer, LedgerEntry, Product
from app.extraction.extractor import RuleExtractor
from app.extraction.schemas import Turn
from tests.conftest import SHOP_ID

CASES = [
    json.loads(line)
    for line in (Path(__file__).parents[1] / "fixtures" / "golden_text_turns.jsonl")
    .read_text(encoding="utf-8").splitlines()
    if line.strip()
]
T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_golden_conversation(case, db, redis_client):
    engine = BasketEngine(db, redis_client, SHOP_ID, RuleExtractor())
    for i, text in enumerate(case["turns"]):
        engine.process_turn(Turn(f"t{i}", text, T0 + timedelta(seconds=20 * i), "A"))

    db.expire_all()
    names = dict(db.execute(select(Product.id, Product.name)).all())
    customers = dict(db.execute(select(Customer.id, Customer.display_name)).all())
    baskets = db.execute(
        select(Basket).where(Basket.status != "cancelled").order_by(Basket.opened_at)
    ).scalars().all()
    assert len(baskets) == len(case["baskets"])
    for got, want in zip(baskets, case["baskets"], strict=True):
        assert got.status == want["status"]
        assert got.payment_mode == want["payment_mode"]
        assert got.total_paise == want["total_paise"]
        assert [(names.get(i.product_id), i.qty) for i in got.items] == [
            (n, Decimal(q)) for n, q in want["items"]]
        if "customer" in want:
            assert customers[got.customer_id] == want["customer"]
        if got.payment_mode == "upi":
            assert got.upi_ref_id and got.upi_link and got.upi_link.startswith("upi://pay?")

    entries = db.execute(select(LedgerEntry).order_by(LedgerEntry.id)).scalars().all()
    assert [(e.entry_type, e.amount_paise, customers.get(e.customer_id)) for e in entries] == [
        tuple(x) for x in case["ledger"]]
    rejected = db.scalar(select(func.count()).select_from(AuditLog)
                         .where(AuditLog.action == "extraction_rejected"))
    assert rejected == case.get("rejected", 0)
