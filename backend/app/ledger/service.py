"""The only writer of ledger_entries.

Append-only: entries are never updated or deleted (a DB trigger enforces this too).
Corrections are 'reversal' entries pointing at the entry they cancel. Balances are derived.
`post` and `reverse` flush but never commit — the caller owns the transaction, so a
ledger posting can be atomic with whatever caused it (a match, a basket close, ...).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import LedgerEntry

EntryType = Literal["sale_cash", "sale_upi", "sale_udhaar", "udhaar_payment", "reversal"]
POSTABLE_TYPES: frozenset[str] = frozenset(
    {"sale_cash", "sale_upi", "sale_udhaar", "udhaar_payment"}
)
NEEDS_CUSTOMER: frozenset[str] = frozenset({"sale_udhaar", "udhaar_payment"})


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class Posting:
    shop_id: int
    entry_type: str
    amount_paise: int
    customer_id: int | None = None
    basket_id: uuid.UUID | None = None
    alert_id: int | None = None
    memo: str | None = None


def udhaar_effect(entry_type: str, amount_paise: int, reversed_type: str | None = None) -> int:
    """Signed change to a customer's udhaar balance caused by one entry."""
    if entry_type == "sale_udhaar":
        return amount_paise
    if entry_type == "udhaar_payment":
        return -amount_paise
    if entry_type == "reversal":
        if reversed_type is None:
            raise LedgerError("reversal needs the reversed entry type")
        return -udhaar_effect(reversed_type, amount_paise)
    return 0


def _validate(p: Posting) -> None:
    if p.entry_type not in POSTABLE_TYPES:
        raise LedgerError(f"cannot post entry_type {p.entry_type!r}; use reverse() for reversals")
    if isinstance(p.amount_paise, bool) or not isinstance(p.amount_paise, int):
        raise LedgerError("amount_paise must be an int")
    if p.amount_paise <= 0:
        raise LedgerError("amount_paise must be positive")
    if p.entry_type in NEEDS_CUSTOMER and p.customer_id is None:
        raise LedgerError(f"{p.entry_type} requires a customer_id")


def post(db: Session, posting: Posting) -> LedgerEntry:
    _validate(posting)
    entry = LedgerEntry(
        shop_id=posting.shop_id,
        entry_type=posting.entry_type,
        amount_paise=posting.amount_paise,
        customer_id=posting.customer_id,
        basket_id=posting.basket_id,
        alert_id=posting.alert_id,
        memo=posting.memo,
    )
    db.add(entry)
    db.flush()
    return entry


def reverse(db: Session, shop_id: int, entry_id: int, memo: str | None = None) -> LedgerEntry:
    original = db.execute(
        select(LedgerEntry).where(LedgerEntry.id == entry_id).with_for_update()
    ).scalar_one_or_none()
    if original is None or original.shop_id != shop_id:
        raise LedgerError(f"ledger entry {entry_id} not found")
    if original.entry_type == "reversal":
        raise LedgerError("a reversal cannot itself be reversed; post a new entry instead")
    already = db.execute(
        select(LedgerEntry.id).where(LedgerEntry.reverses_entry_id == entry_id)
    ).first()
    if already is not None:
        raise LedgerError(f"ledger entry {entry_id} is already reversed")
    entry = LedgerEntry(
        shop_id=shop_id,
        entry_type="reversal",
        amount_paise=original.amount_paise,
        customer_id=original.customer_id,
        basket_id=original.basket_id,
        alert_id=original.alert_id,
        reverses_entry_id=original.id,
        memo=memo or f"reversal of #{original.id}",
    )
    db.add(entry)
    db.flush()
    return entry


def customer_balance(db: Session, shop_id: int, customer_id: int) -> int:
    row = db.execute(
        text(
            "SELECT balance_paise FROM udhaar_balances "
            "WHERE shop_id = :s AND customer_id = :c"
        ),
        {"s": shop_id, "c": customer_id},
    ).first()
    return int(row[0]) if row else 0


def all_balances(db: Session, shop_id: int) -> list[dict[str, object]]:
    rows = db.execute(
        text(
            "SELECT customer_id, display_name, balance_paise, last_payment_at "
            "FROM udhaar_balances WHERE shop_id = :s ORDER BY balance_paise DESC, display_name"
        ),
        {"s": shop_id},
    ).mappings()
    return [dict(r) for r in rows]


def entries(db: Session, shop_id: int, limit: int = 100) -> list[LedgerEntry]:
    return list(
        db.execute(
            select(LedgerEntry)
            .where(LedgerEntry.shop_id == shop_id)
            .order_by(LedgerEntry.id.desc())
            .limit(limit)
        ).scalars()
    )
