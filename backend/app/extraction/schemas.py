"""Typed contract between the LLM and the rest of the system (§5.3).

Nothing reaches a basket or the ledger unless it parses into `ExtractedTurn` *and* passes
`check_business_rules`. Failures raise `ExtractionRejected`, which callers log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Unit = Literal["kg", "g", "l", "ml", "pcs", "packet", "dozen"]
PaymentMode = Literal["cash", "upi", "udhaar", "unknown"]
Action = Literal["add_items", "remove_items", "close_basket", "udhaar_payment", "none"]

# Upper bound on a single spoken line, per unit. Anything above is almost certainly an
# ASR/LLM error ("do sau kilo atta" at a kirana counter).
MAX_QTY: dict[str | None, Decimal] = {
    "kg": Decimal(50), "g": Decimal(50_000), "l": Decimal(50), "ml": Decimal(50_000),
    "pcs": Decimal(200), "packet": Decimal(200), "dozen": Decimal(20), None: Decimal(200),
}
MAX_UDHAAR_PAYMENT_PAISE = 10_000_000  # ₹1 lakh
PRICE_MIN_FACTOR = Decimal("0.5")
PRICE_MAX_FACTOR = Decimal(3)


class ExtractionRejected(ValueError):
    def __init__(self, reason: str, raw: object = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw = raw


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(max_length=200)
    product_hint: str = Field(min_length=1, max_length=100)
    qty: Decimal
    unit: Unit | None = None
    spoken_price_paise: int | None = None  # price per `unit`, if the speaker stated one

    @field_validator("product_hint")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("empty product_hint")
        return v


class ExtractedTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_transactional: bool
    items: list[ExtractedItem] = Field(default_factory=list, max_length=30)
    payment_mode: PaymentMode = "unknown"
    customer_name: str | None = Field(default=None, max_length=100)
    action: Action = "none"
    udhaar_payment_paise: int | None = None


@dataclass(frozen=True)
class Turn:
    """A final ASR turn (or a typed dev turn)."""

    turn_id: str
    text: str
    ts: datetime
    speaker: str | None = None
    is_merchant: bool | None = None


def parse_llm_output(raw: str | dict[str, object]) -> ExtractedTurn:
    try:
        if isinstance(raw, str):
            return ExtractedTurn.model_validate_json(raw)
        return ExtractedTurn.model_validate(raw)
    except ValidationError as exc:
        raise ExtractionRejected(f"schema validation failed: {exc.error_count()} error(s)",
                                 raw) from exc


def check_business_rules(turn: ExtractedTurn) -> None:
    """Rules that need no catalog. The price-band rule runs after catalog matching."""
    for item in turn.items:
        if item.qty <= 0:
            raise ExtractionRejected(f"non-positive qty for {item.product_hint!r}")
        if item.qty > MAX_QTY[item.unit]:
            raise ExtractionRejected(
                f"implausible qty {item.qty} {item.unit or ''} for {item.product_hint!r}")
        if item.spoken_price_paise is not None and item.spoken_price_paise <= 0:
            raise ExtractionRejected(f"non-positive price for {item.product_hint!r}")
    if turn.action == "udhaar_payment":
        amt = turn.udhaar_payment_paise
        if amt is None or amt <= 0 or amt > MAX_UDHAAR_PAYMENT_PAISE:
            raise ExtractionRejected(f"invalid udhaar payment amount {amt!r}")
    if turn.action in ("add_items", "remove_items") and not turn.items:
        raise ExtractionRejected(f"{turn.action} without items")


def check_price_band(hint: str, implied_unit_price: Decimal, catalog_price_paise: int) -> None:
    lo = catalog_price_paise * PRICE_MIN_FACTOR
    hi = catalog_price_paise * PRICE_MAX_FACTOR
    if not lo <= implied_unit_price <= hi:
        raise ExtractionRejected(
            f"spoken price for {hint!r} ({implied_unit_price:.0f}p/unit) is outside "
            f"0.5x-3x of catalog ({catalog_price_paise}p)")
