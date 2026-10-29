"""Spoken name -> customer (§5.8). >= 85 resolves; below that the dashboard asks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, utils
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Customer, CustomerAlias

RESOLVE_THRESHOLD = 85.0
HONORIFICS = {
    "bhaiya", "bhai", "ji", "anna", "amma", "didi", "aunty", "auntie", "uncle", "garu",
    "sir", "madam", "ben", "behen", "saab", "sahab", "akka", "mama", "chacha", "bhabhi",
}


def strip_honorifics(name: str) -> str:
    tokens = [t for t in re.split(r"\s+", utils.default_process(name)) if t]
    kept = [t for t in tokens if t not in HONORIFICS]
    return " ".join(kept or tokens)


@dataclass(frozen=True)
class Resolution:
    customer_id: int | None
    score: float
    candidates: list[tuple[int, str, float]]  # (id, display_name, score), best first


def resolve_customer(db: Session, shop_id: int, spoken: str | None) -> Resolution:
    if not spoken or not spoken.strip():
        return Resolution(None, 0.0, [])
    target = strip_honorifics(spoken)
    terms: dict[int, list[str]] = {}
    names: dict[int, str] = {}
    for c in db.execute(select(Customer).where(Customer.shop_id == shop_id)).scalars():
        names[c.id] = c.display_name
        terms[c.id] = [c.display_name]
    for a in db.execute(select(CustomerAlias).where(CustomerAlias.shop_id == shop_id)).scalars():
        terms.setdefault(a.customer_id, []).append(a.alias)

    scored = []
    for cid, ts in terms.items():
        s = max(fuzz.token_set_ratio(target, strip_honorifics(t)) for t in ts)
        scored.append((cid, names.get(cid, ""), float(s)))
    scored.sort(key=lambda x: -x[2])
    if not scored:
        return Resolution(None, 0.0, [])
    best = scored[0]
    # Two different customers sharing a first name ("Ramesh") is not a resolution.
    tied = len(scored) > 1 and scored[1][2] >= RESOLVE_THRESHOLD and scored[1][2] == best[2]
    resolved = best[0] if best[2] >= RESOLVE_THRESHOLD and not tied else None
    return Resolution(resolved, best[2], scored[:3])


def add_voice_alias(db: Session, shop_id: int, customer_id: int, spoken: str) -> None:
    existing = db.execute(
        select(CustomerAlias.alias).where(CustomerAlias.customer_id == customer_id)
    ).scalars()
    if any(utils.default_process(a) == utils.default_process(spoken) for a in existing):
        return
    db.add(CustomerAlias(shop_id=shop_id, customer_id=customer_id, alias=spoken.strip(),
                         source="voice"))
