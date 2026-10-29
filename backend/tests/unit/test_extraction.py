"""Extractor contract: valid output parses, anything invalid is rejected (never guessed)."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.db.seed import PRODUCTS
from app.extraction.catalog_match import CatalogEntry, convert_qty, match_product
from app.extraction.extractor import (
    LLMExtractor,
    RuleExtractor,
    get_extractor,
    llm_json_schema,
    load_prompt,
)
from app.extraction.schemas import (
    ExtractedItem,
    ExtractedTurn,
    ExtractionRejected,
    Turn,
    check_amounts_grounded,
    check_business_rules,
    check_price_band,
    parse_llm_output,
)

CATALOG = [CatalogEntry(i + 1, n, tuple(a), u, p) for i, (n, a, u, p, *_) in enumerate(PRODUCTS)]
TURN = Turn("t1", "do kilo atta", datetime(2026, 11, 2, tzinfo=UTC), "A")


class FakeLLM:
    def __init__(self, content=None, exc=None):
        self.content, self.exc, self.calls = content, exc, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        msg = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


VALID = {
    "is_transactional": True,
    "items": [{"raw_text": "do kilo atta", "product_hint": "Atta", "qty": 2, "unit": "kg",
               "spoken_price_paise": None}],
    "payment_mode": "unknown", "customer_name": None, "action": "add_items",
    "udhaar_payment_paise": None,
}


def test_llm_valid_output():
    fake = FakeLLM(json.dumps(VALID))
    out = LLMExtractor(fake, "muse-spark-1.3").extract(TURN, [], CATALOG)
    assert out.items[0].product_hint == "atta"
    assert out.items[0].qty == Decimal(2)
    call = fake.calls[0]
    assert call["model"] == "muse-spark-1.3" and call["temperature"] == 0
    assert call["response_format"]["type"] == "json_schema"
    assert "Atta: aata" in call["messages"][0]["content"]  # catalog injected into prompt
    assert "do kilo atta" in call["messages"][1]["content"]


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps({**VALID, "items": [{**VALID["items"][0], "qty": "lots"}]}),
        json.dumps({**VALID, "payment_mode": "bitcoin"}),
        json.dumps({**VALID, "surprise": 1}),
        json.dumps({**VALID, "items": [{**VALID["items"][0], "qty": 0}]}),
        json.dumps({**VALID, "items": [{**VALID["items"][0], "qty": 500}]}),
        json.dumps({**VALID, "action": "udhaar_payment", "items": []}),
        json.dumps({**VALID, "items": [{**VALID["items"][0], "spoken_price_paise": -5}]}),
        json.dumps({**VALID, "items": []}),  # add_items with nothing to add
        "",
    ],
)
def test_llm_invalid_output_rejected(content):
    with pytest.raises(ExtractionRejected):
        LLMExtractor(FakeLLM(content), "m").extract(TURN, [], CATALOG)


def test_llm_100x_repayment_slip_is_rejected():
    # Real muse-spark output seen during the eval: Rs 500 returned as 5,000,000 paise.
    turn = Turn("t", "Suresh anna 500 rupaye jama kar rahe hain", TURN.ts)
    slip = {**VALID, "items": [], "action": "udhaar_payment", "payment_mode": "cash",
            "customer_name": "Suresh anna", "udhaar_payment_paise": 5_000_000}
    with pytest.raises(ExtractionRejected, match="matches no number"):
        LLMExtractor(FakeLLM(json.dumps(slip)), "m").extract(turn, [], CATALOG)
    ok = {**slip, "udhaar_payment_paise": 50_000}
    assert LLMExtractor(FakeLLM(json.dumps(ok)), "m").extract(turn, [], CATALOG) \
        .udhaar_payment_paise == 50_000


@pytest.mark.parametrize(
    ("text", "price", "qty", "ok"),
    [("do kilo atta 90 rupaye", 4500, "2", True),   # 90 is the line total
     ("atta 45 rupaye kilo", 4500, "2", True),       # 45 is the unit price
     ("do kilo atta 90 rupaye", 450000, "2", False),  # 100x slip
     ("do kilo atta nabbe rupaye", 4500, "2", True),  # words only: not checkable here
     ("1,250 ka saamaan", 125000, "1", True)],
)
def test_check_amounts_grounded_prices(text, price, qty, ok):
    turn = ExtractedTurn(is_transactional=True, action="add_items", items=[
        ExtractedItem(raw_text=text, product_hint="atta", qty=Decimal(qty),
                      spoken_price_paise=price)])
    if ok:
        check_amounts_grounded(text, turn)
    else:
        with pytest.raises(ExtractionRejected):
            check_amounts_grounded(text, turn)


def test_llm_schema_requires_every_field_without_defaults():
    schema = llm_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    item = schema["$defs"]["ExtractedItem"]
    assert set(item["required"]) == set(item["properties"])
    assert "default" not in json.dumps(schema)
    assert "rupees x 100" in schema["properties"]["udhaar_payment_paise"]["description"]


def test_llm_api_error_rejected():
    with pytest.raises(ExtractionRejected, match="LLM call failed"):
        LLMExtractor(FakeLLM(exc=TimeoutError()), "m").extract(TURN, [], CATALOG)


def test_parse_llm_output_accepts_dict():
    assert isinstance(parse_llm_output(VALID), ExtractedTurn)


def test_udhaar_payment_bounds():
    ok = ExtractedTurn(is_transactional=True, action="udhaar_payment", udhaar_payment_paise=500)
    check_business_rules(ok)
    with pytest.raises(ExtractionRejected):
        check_business_rules(ok.model_copy(update={"udhaar_payment_paise": 10**9}))


def test_price_band():
    check_price_band("atta", Decimal(4500), 4500)
    check_price_band("atta", Decimal(2250), 4500)
    with pytest.raises(ExtractionRejected):
        check_price_band("atta", Decimal(2249), 4500)
    with pytest.raises(ExtractionRejected):
        check_price_band("atta", Decimal(13501), 4500)


def test_prompt_is_versioned():
    assert "{catalog}" in load_prompt("v1")
    assert LLMExtractor(FakeLLM(), "m").prompt_version == "v1"
    assert RuleExtractor.prompt_version == "rules-v1"


def test_get_extractor_falls_back_without_key(settings):
    s = settings.model_copy(update={"extractor_backend": "llm", "model_api_key": ""})
    assert isinstance(get_extractor(s), RuleExtractor)
    s = settings.model_copy(update={"extractor_backend": "llm", "model_api_key": "x"})
    assert isinstance(get_extractor(s), LLMExtractor)


@pytest.mark.parametrize(
    ("hint", "product", "flagged"),
    [("atta", "Atta", False), ("aata", "Atta", False), ("Toor Dal", "Toor Dal", False),
     ("chawal", "Sona Masoori Rice", False), ("basmati", "Basmati Rice", False),
     ("thumsup", "Cold Drink 750ml", False), ("dal", "Toor Dal", True),
     ("pyaj", "Onion", True), ("unobtainium", None, True), ("", None, True)],
)
def test_match_product(hint, product, flagged):
    m = match_product(hint, CATALOG)
    assert (m.product.name if m.product else None) == product
    assert m.flagged == flagged


@pytest.mark.parametrize(
    ("qty", "unit", "product_unit", "out", "ok"),
    [("500", "g", "kg", "0.5", True), ("250", "ml", "l", "0.25", True),
     ("2", "dozen", "pcs", "24", True), ("6", "pcs", "dozen", "0.5", True),
     ("2", None, "kg", "2", True), ("3", "packet", "pcs", "3", True),
     ("500", "g", "packet", "500", False)],
)
def test_convert_qty(qty, unit, product_unit, out, ok):
    assert convert_qty(Decimal(qty), unit, product_unit) == (Decimal(out), ok)


@pytest.mark.parametrize(
    ("text", "mode", "action", "customer"),
    [("bas, kitna hua? GPay karta hoon", "upi", "close_basket", None),
     ("Ramesh bhaiya ke khate mein likh do", "udhaar", "close_basket", "ramesh bhaiya"),
     ("Lakshmi ke khate mein", "udhaar", "close_basket", "lakshmi"),
     ("irfan bhai ke khate mein", "udhaar", "close_basket", "irfan bhai"),
     ("ek kilo atta", "unknown", "add_items", None),
     ("haan ji theek hai", "unknown", "none", None)],
)
def test_rule_extractor_cues(text, mode, action, customer):
    out = RuleExtractor().extract(Turn("t", text, TURN.ts), [], CATALOG)
    assert (out.payment_mode, out.action, out.customer_name) == (mode, action, customer)
