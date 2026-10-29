"""Phase 1 acceptance: migrations apply, seed loads, ledger is append-only at the DB level."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import Customer, LedgerEntry, Product, Shop, Vendor
from tests.conftest import SHOP_ID


def _insert_entry(db) -> int:
    db.execute(
        text(
            "INSERT INTO ledger_entries (shop_id, entry_type, amount_paise, memo) "
            "VALUES (:s, 'sale_cash', 15000, 'test')"
        ),
        {"s": SHOP_ID},
    )
    db.commit()
    return db.execute(select(func.max(LedgerEntry.id))).scalar_one()


def test_seed_counts(db):
    assert db.get(Shop, SHOP_ID) is not None
    assert db.scalar(select(func.count()).select_from(Product)) == 40
    assert db.scalar(select(func.count()).select_from(Vendor)) == 3
    assert db.scalar(select(func.count()).select_from(Customer)) == 10


def test_ledger_update_is_rejected(db):
    entry_id = _insert_entry(db)
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(
            text("UPDATE ledger_entries SET amount_paise = 1 WHERE id = :i"), {"i": entry_id}
        )
        db.commit()
    db.rollback()
    assert db.get(LedgerEntry, entry_id).amount_paise == 15000


def test_ledger_delete_is_rejected(db):
    entry_id = _insert_entry(db)
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM ledger_entries WHERE id = :i"), {"i": entry_id})
        db.commit()
    db.rollback()
    assert db.get(LedgerEntry, entry_id) is not None


def test_ledger_rejects_non_positive_amounts(db):
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO ledger_entries (shop_id, entry_type, amount_paise) "
                "VALUES (:s, 'sale_cash', 0)"
            ),
            {"s": SHOP_ID},
        )
        db.commit()
    db.rollback()


def test_reversal_requires_reference(db):
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO ledger_entries (shop_id, entry_type, amount_paise) "
                "VALUES (:s, 'reversal', 100)"
            ),
            {"s": SHOP_ID},
        )
        db.commit()
    db.rollback()


def test_alert_idempotency_key_unique(db):
    sql = text(
        "INSERT INTO upi_alerts (shop_id, idempotency_key, amount_paise, received_at, source, "
        "status) VALUES (:s, 'dup-1', 100, now(), 'simulated', 'unmatched')"
    )
    db.execute(sql, {"s": SHOP_ID})
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(sql, {"s": SHOP_ID})
        db.commit()
    db.rollback()
