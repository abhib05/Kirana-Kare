"""Paise arithmetic. Money is always an int number of paise; floats never touch it."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PAISE_PER_RUPEE = 100


class MoneyError(ValueError):
    pass


def to_decimal(value: str | int | Decimal) -> Decimal:
    if isinstance(value, (bool, float)):
        raise MoneyError("floats/bools are not accepted for money; pass str, int or Decimal")
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise MoneyError(f"not a number: {value!r}") from exc
    if not d.is_finite():
        raise MoneyError(f"not a finite number: {value!r}")
    return d


def rupees_to_paise(rupees: str | int | Decimal) -> int:
    """'150.50' -> 15050. Rounds half-up to the nearest paisa."""
    d = to_decimal(rupees) * PAISE_PER_RUPEE
    return int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def paise_to_rupees(paise: int) -> Decimal:
    return (Decimal(paise) / PAISE_PER_RUPEE).quantize(Decimal("0.01"))


def format_inr(paise: int) -> str:
    """15050 -> '₹150.50', 15000 -> '₹150', -500 -> '-₹5'."""
    sign = "-" if paise < 0 else ""
    rupees, rem = divmod(abs(paise), PAISE_PER_RUPEE)
    whole = f"{rupees:,}"
    return f"{sign}₹{whole}" if rem == 0 else f"{sign}₹{whole}.{rem:02d}"


def upi_amount(paise: int) -> str:
    """UPI deep links want rupees with exactly two decimals: 15050 -> '150.50'."""
    if paise <= 0:
        raise MoneyError("UPI amount must be positive")
    return f"{paise_to_rupees(paise):.2f}"


def line_total(qty: Decimal, unit_price_paise: int) -> int:
    """qty × unit price, rounded half-up to whole paise."""
    return int((Decimal(qty) * unit_price_paise).quantize(Decimal(1), rounding=ROUND_HALF_UP))
