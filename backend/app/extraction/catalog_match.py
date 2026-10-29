"""Map a spoken product hint to a catalog SKU with RapidFuzz, and convert spoken units.

Score >= 85 auto-maps; 70-85 maps but flags the line for review; < 70 stays unmapped.
token_set_ratio scores a subset as 100 ("dal" vs "toor dal"), so when two *different*
products both clear the bar and neither is an exact name/alias hit, the line is treated
as ambiguous and capped into the flagged band.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rapidfuzz import fuzz, utils

AUTO_MAP = 85.0
FLAG_MAP = 70.0
AMBIGUOUS_CAP = 80.0


@dataclass(frozen=True)
class CatalogEntry:
    id: int
    name: str
    aliases: tuple[str, ...]
    unit: str
    price_paise: int

    @property
    def terms(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class CatalogMatch:
    product: CatalogEntry | None
    score: float
    flagged: bool


def _norm(s: str) -> str:
    return utils.default_process(s)


def match_product(hint: str, catalog: list[CatalogEntry]) -> CatalogMatch:
    h = _norm(hint)
    if not h:
        return CatalogMatch(None, 0.0, True)
    scored: list[tuple[float, float, bool, CatalogEntry]] = []
    for p in catalog:
        best_set, best_plain, exact = 0.0, 0.0, False
        for term in p.terms:
            t = _norm(term)
            exact = exact or t == h
            best_set = max(best_set, fuzz.token_set_ratio(h, t))
            best_plain = max(best_plain, fuzz.ratio(h, t))
        scored.append((best_set, best_plain, exact, p))
    scored.sort(key=lambda x: (x[2], x[0], x[1]), reverse=True)
    top_set, _, top_exact, top = scored[0]

    if top_set < FLAG_MAP:
        return CatalogMatch(None, top_set, True)
    score = top_set
    if not top_exact and len(scored) > 1 and scored[1][0] >= AUTO_MAP:
        score = min(score, AMBIGUOUS_CAP)
    return CatalogMatch(top, score, score < AUTO_MAP)


_CONVERSIONS: dict[tuple[str, str], Decimal] = {
    ("g", "kg"): Decimal("0.001"),
    ("kg", "g"): Decimal(1000),
    ("ml", "l"): Decimal("0.001"),
    ("l", "ml"): Decimal(1000),
    ("pcs", "packet"): Decimal(1),
    ("packet", "pcs"): Decimal(1),
    ("dozen", "pcs"): Decimal(12),
    ("pcs", "dozen"): Decimal(1) / Decimal(12),
}


def convert_qty(qty: Decimal, unit: str | None, product_unit: str) -> tuple[Decimal, bool]:
    """Spoken qty -> qty in the product's unit. Returns (qty, compatible)."""
    if unit is None or unit == product_unit:
        return qty, True
    factor = _CONVERSIONS.get((unit, product_unit))
    if factor is None:
        return qty, False
    return (qty * factor).quantize(Decimal("0.001")), True
