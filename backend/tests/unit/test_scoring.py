"""Table-driven tests for every §5.6 edge case. Pure logic, no database."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.reconciliation.scoring import (
    AlertView,
    BasketView,
    CustomerView,
    decide,
    name_score,
    score_candidates,
)

T0 = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)
B1, B2, B3, B4 = (uuid.UUID(int=i) for i in range(1, 5))

RAMESH = CustomerView(1, ("Ramesh Kumar", "ramesh bhaiya", "RAMESH K"), 0)
SURESH = CustomerView(2, ("Suresh Reddy", "suresh anna", "SURESH REDDY M"), 40000)
LAKSHMI = CustomerView(3, ("Lakshmi Devi", "lakshmi amma"), 0)
CUSTOMERS = [RAMESH, SURESH, LAKSHMI]


def alert(amount=15000, minutes=0.0, payer=None, ref=None):
    return AlertView(id=1, amount_paise=amount, received_at=T0 + timedelta(minutes=minutes),
                     payer_name=payer, ref_id=ref)


def basket(bid, total=15000, minutes=0.0, ref=None, customer=None):
    return BasketView(id=bid, total_paise=total, ref_time=T0 + timedelta(minutes=minutes),
                      upi_ref_id=ref, customer_id=customer)


CASES = [
    # id, alert, baskets, expected outcome, expected best basket, expected best confidence
    ("tier1_ref", alert(ref="KKABCDEF"), [basket(B1, total=999, ref="KKABCDEF")],
     "auto_match", B1, 0.99),
    ("tier1_ref_case_insensitive", alert(ref="kkabcdef"), [basket(B1, ref="KKABCDEF")],
     "auto_match", B1, 0.99),
    ("tier1_beats_same_amount_basket", alert(ref="KKABCDEF"),
     [basket(B1, ref="KKABCDEF", minutes=-9), basket(B2)], "auto_match", B1, 0.99),
    ("tier2_unique_immediate", alert(minutes=1), [basket(B1)], "auto_match", B1, 0.85),
    ("tier2_unique_3min", alert(minutes=3), [basket(B1)], "auto_match", B1, 0.80),
    ("tier2_unique_4min_below_threshold", alert(minutes=4), [basket(B1)],
     "pending_confirm", B1, 0.75),
    ("tier2_late_but_name_matches", alert(minutes=6, payer="RAMESH K"),
     [basket(B1, customer=1)], "auto_match", B1, 0.80),
    ("tier2_alert_before_basket_awaiting", alert(minutes=-1), [basket(B1)],
     "auto_match", B1, 0.85),
    ("tier2_two_same_amount_tie", alert(), [basket(B1), basket(B2, minutes=1)],
     "pending_confirm", B1, 0.50),
    ("tier2_three_same_amount", alert(), [basket(B1), basket(B2), basket(B3)],
     "pending_confirm", None, 0.25),
    ("same_amount_name_disambiguates_but_not_enough", alert(payer="Suresh Reddy"),
     [basket(B1, customer=1), basket(B2, customer=2)], "pending_confirm", B2, 0.65),
    ("payer_is_different_customer", alert(payer="Lakshmi Devi"),
     [basket(B1, customer=1)], "pending_confirm", B1, 0.75),
    ("payer_known_but_basket_anonymous", alert(payer="Lakshmi Devi"),
     [basket(B1)], "pending_confirm", B1, 0.75),
    ("payer_unknown_name_no_penalty", alert(payer="ZZ TOP"), [basket(B1)],
     "auto_match", B1, 0.85),
    ("outside_window", alert(minutes=11), [basket(B1)], "pending_confirm", None, None),
    ("amount_off_by_one_paisa", alert(amount=15001), [basket(B1)],
     "pending_confirm", None, None),
    ("name_only_partial_payment", alert(amount=10000, payer="ramesh"),
     [basket(B1, customer=1)], "pending_confirm", B1, 0.50),
    ("no_baskets", alert(), [], "pending_confirm", None, None),
]


@pytest.mark.parametrize(("name", "a", "baskets", "outcome", "best", "conf"), CASES,
                         ids=[c[0] for c in CASES])
def test_decision_table(name, a, baskets, outcome, best, conf):
    d = decide(a, baskets, CUSTOMERS, window_minutes=10, threshold=0.80)
    assert d.outcome == outcome
    if best is not None:
        assert d.best is not None and d.best.basket_id == best
    if conf is not None:
        assert d.best is not None
        assert d.best.confidence == pytest.approx(conf, abs=1e-9)
    if conf is None:
        assert d.best is None


def test_three_same_amount_all_penalised_equally():
    cands = score_candidates(alert(), [basket(B1), basket(B2), basket(B3)], CUSTOMERS, 10)
    assert [c.confidence for c in cands] == pytest.approx([0.25, 0.25, 0.25])


def test_ambiguous_best_never_auto_matches_even_above_threshold():
    d = decide(alert(), [basket(B1), basket(B2)], CUSTOMERS, threshold=0.40)
    assert d.outcome == "pending_confirm"
    assert "ambiguous" in d.reason


def test_confidence_is_clamped_to_zero():
    many = [basket(uuid.uuid4()) for _ in range(6)]
    cands = score_candidates(alert(minutes=9, payer="Lakshmi Devi"), many, CUSTOMERS, 10)
    assert all(c.confidence == 0.0 for c in cands)


def test_top_three_only():
    many = [basket(uuid.uuid4()) for _ in range(5)]
    d = decide(alert(), many, CUSTOMERS)
    assert len(d.candidates) == 3


def test_udhaar_repayment_suggested_when_payer_owes():
    d = decide(alert(amount=40000, payer="SURESH REDDY M"), [], CUSTOMERS)
    assert d.outcome == "pending_confirm"
    assert d.suggested_customer_id == 2


def test_no_udhaar_suggestion_without_balance():
    d = decide(alert(payer="RAMESH K"), [], CUSTOMERS)
    assert d.suggested_customer_id is None


def test_reminder_ref_is_tier1_udhaar_payment():
    d = decide(alert(ref="KKREMIND"), [basket(B1)], CUSTOMERS,
               reminder_refs={"KKREMIND": 2})
    assert d.outcome == "auto_udhaar_payment"
    assert d.suggested_customer_id == 2


def test_name_score():
    assert name_score("RAMESH K", RAMESH.aliases) >= 80
    assert name_score("ramesh kumar", RAMESH.aliases) == 100
    assert name_score(None, RAMESH.aliases) == 0
    assert name_score("Lakshmi Devi", RAMESH.aliases) < 80


def test_decision_serialises():
    d = decide(alert(), [basket(B1)], CUSTOMERS)
    out = d.as_dict()
    assert out["outcome"] == "auto_match"
    assert out["best"]["basket_id"] == str(B1)
    assert out["candidates"][0]["tier"] == 2
