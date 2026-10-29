from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.money import (
    MoneyError,
    format_inr,
    line_total,
    paise_to_rupees,
    rupees_to_paise,
    upi_amount,
)


@pytest.mark.parametrize(
    ("rupees", "paise"),
    [("150.50", 15050), ("150", 15000), (150, 15000), ("1,250.5", 125050), ("0.005", 1),
     ("0.004", 0), (Decimal("99.99"), 9999), (" 12.30 ", 1230)],
)
def test_rupees_to_paise(rupees, paise):
    assert rupees_to_paise(rupees) == paise


@pytest.mark.parametrize("bad", [150.5, True, "abc", "NaN", "inf"])
def test_rupees_to_paise_rejects_floats_and_junk(bad):
    with pytest.raises(MoneyError):
        rupees_to_paise(bad)


@pytest.mark.parametrize(
    ("paise", "text"),
    [(15050, "₹150.50"), (15000, "₹150"), (5, "₹0.05"), (-500, "-₹5"), (12345678, "₹123,456.78")],
)
def test_format_inr(paise, text):
    assert format_inr(paise) == text


def test_upi_amount():
    assert upi_amount(15050) == "150.50"
    assert upi_amount(15000) == "150.00"
    with pytest.raises(MoneyError):
        upi_amount(0)


@pytest.mark.parametrize(
    ("qty", "price", "total"),
    [("2", 4500, 9000), ("0.5", 4500, 2250), ("0.25", 3333, 833), ("1.5", 1001, 1502),
     ("0.333", 3000, 999)],
)
def test_line_total(qty, price, total):
    assert line_total(Decimal(qty), price) == total


@given(st.integers(min_value=0, max_value=10**12))
def test_paise_roundtrip(paise):
    assert rupees_to_paise(paise_to_rupees(paise)) == paise
