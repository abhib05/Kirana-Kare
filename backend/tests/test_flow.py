import time

import pytest

from app import alerts, extractor
from app.db import Database
from app.engine import Engine
from app.seed import seed


@pytest.fixture()
def eng():
    db = Database(":memory:")
    seed(db)
    return Engine(db)


def items(eng):
    return eng.items()


def test_extract_hindi_basket(eng):
    ex = extractor.extract_rules("ek kilo cheeni dena aur do packet maggi", items(eng))
    assert ex.is_transaction
    got = {l.name: l.qty for l in ex.lines}
    assert got == {"Sugar": 1, "Maggi": 2}
    assert ex.total == 46 + 28


def test_extract_kannada_telugu(eng):
    ex = extractor.extract_rules("onde kg sakkare beku", items(eng))
    assert ex.lang == "kn" and ex.lines[0].name == "Sugar"
    ex = extractor.extract_rules("rendu kilo biyyam ivvandi", items(eng))
    assert ex.lang == "te" and ex.lines[0].qty == 2 and ex.lines[0].name == "Rice"


def test_chitchat_discarded(eng):
    for t in ["aaj bahut garmi hai bhaiya", "cricket match dekha kal?", "kitna hua"]:
        assert not extractor.extract_rules(t, items(eng)).is_transaction


def test_do_is_not_always_two(eng):
    ex = extractor.extract_rules("ek kilo cheeni de do", items(eng))
    assert [(l.name, l.qty) for l in ex.lines] == [("Sugar", 1)]


def test_udhaar_and_customer(eng):
    ex = extractor.extract_rules("do packet maggi Ramesh ke khate mein likh do", items(eng))
    assert ex.udhaar and ex.customer == "Ramesh"
    ex = extractor.extract_rules("ek kilo cheeni khate mein likh do Sunita", items(eng))
    assert ex.udhaar and ex.customer == "Sunita"


def test_parse_alerts():
    a = alerts.parse_alert("You've received ₹150.00 from RAMESH KUMAR via PhonePe. UPI Ref: 4238829912")
    assert a["amount"] == 150 and a["payer"] == "RAMESH KUMAR" and a["app"] == "PhonePe"
    assert a["upi_ref"] == "4238829912"
    a = alerts.parse_alert("Rs 460.00 credited to A/c XX1234 on 05-10 by UPI. Received from Sunita S. Ref KC-C4")
    assert a["amount"] == 460 and a["tag_ref"] == "KC-C4"
    assert alerts.parse_alert("Rs 50 debited from your account for UPI payment") is None


def test_upi_auto_match_deducts_stock(eng):
    before = next(i for i in eng.items() if i["name"] == "Sugar")["stock"]
    r = eng.handle_utterance("ek kilo cheeni dena")
    assert r["basket"]["total"] == 46 and r["basket"]["status"] == "open"
    out = eng.handle_alert(raw="Received ₹46 from Mohan via GPay. UPI Ref: 99112233")
    assert out["alert"]["status"] == "matched" and out["alert"]["confidence"] >= 0.8
    assert eng.basket(r["basket"]["id"])["status"] == "paid_upi"
    assert next(i for i in eng.items() if i["name"] == "Sugar")["stock"] == before - 1


def test_alert_before_speech_pairs_later(eng):
    eng.handle_alert(raw="Received ₹28 from Mohan via PhonePe. UPI Ref: 55667788")
    r = eng.handle_utterance("do packet maggi")
    assert eng.basket(r["basket"]["id"])["status"] == "paid_upi"


def test_stale_basket_needs_confirm(eng):
    now = time.time()
    r = eng.handle_utterance("ek kilo cheeni", now=now)
    out = eng.handle_alert(raw="Received ₹46 from Mohan via GPay. UPI Ref: 1234567", now=now + 400)
    assert out["alert"]["status"] == "needs_confirm"
    eng.confirm_alert(out["alert"]["id"])
    assert eng.basket(r["basket"]["id"])["status"] == "paid_upi"


def test_reject_returns_to_unmatched(eng):
    now = time.time()
    eng.handle_utterance("ek kilo cheeni", now=now)
    out = eng.handle_alert(raw="Received ₹46 from Mohan via GPay. UPI Ref: 1234567", now=now + 400)
    assert eng.reject_alert(out["alert"]["id"])["status"] == "unmatched"


def test_same_amount_ambiguity_lowers_confidence(eng):
    now = time.time()
    eng.handle_utterance("ek kilo cheeni", now=now)
    eng.handle_utterance("ek kilo cheeni", now=now + 5)
    out = eng.handle_alert(raw="Received ₹46 from Mohan via GPay. UPI Ref: 1234567", now=now + 10)
    assert out["alert"]["status"] == "needs_confirm"


def test_duplicate_alert_ignored(eng):
    eng.handle_utterance("ek kilo cheeni")
    eng.handle_alert(raw="Received ₹46 from Mohan via PhonePe. UPI Ref: 77889900")
    d = eng.handle_alert(raw="Rs 46.00 credited by UPI from Mohan. UPI Ref 77889900")
    assert d["alert"]["status"] == "duplicate"


def test_udhaar_then_tagged_payment_clears_debt(eng):
    r = eng.handle_utterance("ek kilo cheeni Anil ke khate mein likh do")
    assert r["basket"]["status"] == "udhaar"
    anil = next(c for c in eng.customers() if c["name"] == "Anil")
    assert anil["balance"] == 46
    link = eng.paylink(anil["id"])
    assert "tn=KC-C5" in link["link"] and "am=46.00" in link["link"]
    out = eng.handle_alert(raw=f"Received ₹46 from Anil Verma via PhonePe. Note {link['ref']}")
    assert out["alert"]["status"] == "cleared_udhaar"
    assert next(c for c in eng.customers() if c["name"] == "Anil")["balance"] == 0


def test_untagged_payment_from_known_debtor(eng):
    out = eng.handle_alert(raw="Received ₹95 from Sunita Sharma via GPay. UPI Ref: 31313131")
    assert out["alert"]["status"] == "cleared_udhaar"


def test_udhaar_without_name_waits_for_customer(eng):
    r = eng.handle_utterance("ek kilo cheeni khate mein likh do")
    assert r["basket"]["status"] == "needs_customer"
    b = eng.assign_customer(r["basket"]["id"], 1)
    assert b["status"] == "udhaar"


def test_reminders_respect_age_and_repeat(eng):
    sent = eng.run_reminders(min_age_days=3)
    names = {s["customer"] for s in sent}
    assert names == {"Ramesh", "Lakshmi", "Venkat"}  # Sunita's udhaar is 1 day old
    assert "udhaar baaki" in next(s["message"] for s in sent if s["customer"] == "Ramesh")
    assert "baaki ide" in next(s["message"] for s in sent if s["customer"] == "Lakshmi")
    assert eng.run_reminders(min_age_days=3) == []  # no nagging


def test_forecast_flags_fast_movers(eng):
    f = {x["name"]: x for x in eng.forecast()}
    assert f["Eggs"]["low"] or f["Bread"]["low"]
    assert f["Milk"]["daily_avg"] > f["Salt"]["daily_avg"]


def test_summary(eng):
    eng.handle_utterance("ek kilo cheeni")
    eng.handle_alert(raw="Received ₹46 from Mohan via PhonePe. UPI Ref: 12121212")
    s = eng.summary()
    assert s["today_sales"] == 46 and s["auto_match_rate"] == 1.0
