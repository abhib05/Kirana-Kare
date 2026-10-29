"""Ledger service: postings, reversals, and property-based balance invariants."""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select, text

from app.db.models import LedgerEntry
from app.ledger import service as ledger
from app.ledger.service import LedgerError, Posting, udhaar_effect
from app.udhaar.service import UdhaarError, record_payment
from tests.conftest import SHOP_ID

CUSTOMERS = [1, 2, 3]


def test_post_and_balance(db):
    ledger.post(db, Posting(SHOP_ID, "sale_udhaar", 25000, customer_id=1))
    ledger.post(db, Posting(SHOP_ID, "udhaar_payment", 10000, customer_id=1))
    ledger.post(db, Posting(SHOP_ID, "sale_cash", 5000))
    db.commit()
    assert ledger.customer_balance(db, SHOP_ID, 1) == 15000
    assert ledger.customer_balance(db, SHOP_ID, 2) == 0


@pytest.mark.parametrize(
    "posting",
    [
        Posting(SHOP_ID, "sale_udhaar", 100),  # no customer
        Posting(SHOP_ID, "udhaar_payment", 100),  # no customer
        Posting(SHOP_ID, "sale_cash", 0),
        Posting(SHOP_ID, "sale_cash", -5),
        Posting(SHOP_ID, "reversal", 100),
        Posting(SHOP_ID, "refund", 100),
        Posting(SHOP_ID, "sale_cash", 1.5),  # type: ignore[arg-type]
        Posting(SHOP_ID, "sale_cash", True),  # type: ignore[arg-type]
    ],
)
def test_post_validation(db, posting):
    with pytest.raises(LedgerError):
        ledger.post(db, posting)


def test_reversal_restores_balance_and_keeps_original(db):
    sale = ledger.post(db, Posting(SHOP_ID, "sale_udhaar", 25000, customer_id=1))
    db.commit()
    rev = ledger.reverse(db, SHOP_ID, sale.id, "wrong customer")
    db.commit()
    assert rev.entry_type == "reversal"
    assert rev.reverses_entry_id == sale.id
    assert rev.customer_id == 1
    assert ledger.customer_balance(db, SHOP_ID, 1) == 0
    assert db.get(LedgerEntry, sale.id).amount_paise == 25000  # original untouched


def test_reversing_a_payment_adds_back(db):
    ledger.post(db, Posting(SHOP_ID, "sale_udhaar", 30000, customer_id=2))
    pay = ledger.post(db, Posting(SHOP_ID, "udhaar_payment", 30000, customer_id=2))
    db.commit()
    ledger.reverse(db, SHOP_ID, pay.id)
    db.commit()
    assert ledger.customer_balance(db, SHOP_ID, 2) == 30000


def test_cannot_reverse_twice_or_reverse_a_reversal(db):
    sale = ledger.post(db, Posting(SHOP_ID, "sale_cash", 1000))
    db.commit()
    rev = ledger.reverse(db, SHOP_ID, sale.id)
    db.commit()
    with pytest.raises(LedgerError, match="already reversed"):
        ledger.reverse(db, SHOP_ID, sale.id)
    with pytest.raises(LedgerError, match="cannot itself be reversed"):
        ledger.reverse(db, SHOP_ID, rev.id)
    with pytest.raises(LedgerError, match="not found"):
        ledger.reverse(db, SHOP_ID, 999_999)


def test_record_payment_unknown_customer(db):
    with pytest.raises(UdhaarError):
        record_payment(db, SHOP_ID, 999, 100)


def test_daily_summary_nets_reversals(db):
    ledger.post(db, Posting(SHOP_ID, "sale_cash", 1000))
    upi = ledger.post(db, Posting(SHOP_ID, "sale_upi", 2000))
    ledger.post(db, Posting(SHOP_ID, "sale_udhaar", 3000, customer_id=1))
    ledger.post(db, Posting(SHOP_ID, "udhaar_payment", 500, customer_id=1))
    db.commit()
    ledger.reverse(db, SHOP_ID, upi.id)
    db.commit()
    row = db.execute(
        text("SELECT cash_paise, upi_paise, udhaar_paise, udhaar_collected_paise "
             "FROM daily_summary WHERE shop_id = :s"), {"s": SHOP_ID}
    ).one()
    assert tuple(row) == (1000, 0, 3000, 500)


def test_udhaar_effect_requires_reversed_type():
    with pytest.raises(LedgerError):
        udhaar_effect("reversal", 100)
    assert udhaar_effect("sale_cash", 100) == 0


# --- property-based invariants ------------------------------------------------------------

op = st.one_of(
    st.tuples(st.just("sale_udhaar"), st.sampled_from(CUSTOMERS), st.integers(1, 500_000)),
    st.tuples(st.just("udhaar_payment"), st.sampled_from(CUSTOMERS), st.integers(1, 500_000)),
    st.tuples(st.just("sale_cash"), st.none(), st.integers(1, 500_000)),
    st.tuples(st.just("sale_upi"), st.none(), st.integers(1, 500_000)),
    st.tuples(st.just("reverse"), st.none(), st.integers(0, 10_000)),
)


def _view_balances(db) -> dict[int, int]:
    rows = db.execute(
        text("SELECT customer_id, balance_paise FROM udhaar_balances WHERE shop_id = :s"),
        {"s": SHOP_ID},
    )
    return {cid: bal for cid, bal in rows if cid in CUSTOMERS}


@settings(max_examples=40, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(ops=st.lists(op, min_size=1, max_size=25))
def test_balances_match_model_for_any_sequence(session_factory, ops):
    with session_factory() as db:
        db.execute(text("TRUNCATE ledger_entries RESTART IDENTITY CASCADE"))
        db.commit()
        model = dict.fromkeys(CUSTOMERS, 0)
        posted: list[LedgerEntry] = []
        reversed_ids: set[int] = set()

        for kind, customer, n in ops:
            before = _view_balances(db)
            if kind == "reverse":
                candidates = [e for e in posted
                              if e.entry_type != "reversal" and e.id not in reversed_ids]
                if not candidates:
                    continue
                target = candidates[n % len(candidates)]
                entry = ledger.reverse(db, SHOP_ID, target.id)
                reversed_ids.add(target.id)
                if target.customer_id is not None:
                    model[target.customer_id] += udhaar_effect(
                        "reversal", target.amount_paise, target.entry_type)
            else:
                entry = ledger.post(db, Posting(SHOP_ID, kind, n, customer_id=customer))
                if customer is not None:
                    model[customer] += udhaar_effect(kind, n)
            posted.append(entry)
            db.commit()

            after = _view_balances(db)
            # No balance moves except the customer named on the new entry.
            changed = {c for c in CUSTOMERS if before[c] != after[c]}
            assert changed <= ({entry.customer_id} - {None})

        assert _view_balances(db) == model
        # Sum of balances equals the net udhaar effect of every entry in the ledger.
        rows = db.execute(select(LedgerEntry)).scalars().all()
        by_id = {e.id: e for e in rows}
        net = sum(
            udhaar_effect(e.entry_type, e.amount_paise,
                          by_id[e.reverses_entry_id].entry_type if e.reverses_entry_id else None)
            for e in rows
        )
        assert sum(model.values()) == net
        assert len(rows) == len(posted)
