"""Udhaar (customer credit) operations on top of the ledger."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AuditLog, Customer, LedgerEntry
from app.ledger import service as ledger


class UdhaarError(ValueError):
    pass


def record_payment(
    db: Session,
    shop_id: int,
    customer_id: int,
    amount_paise: int,
    *,
    alert_id: int | None = None,
    actor: str = "merchant",
    memo: str | None = None,
) -> LedgerEntry:
    """Post an udhaar repayment (cash at the counter, or a UPI alert the merchant assigned).
    Flushes only; the caller commits."""
    customer = db.get(Customer, customer_id)
    if customer is None or customer.shop_id != shop_id:
        raise UdhaarError(f"customer {customer_id} not found")
    entry = ledger.post(
        db,
        ledger.Posting(
            shop_id=shop_id,
            entry_type="udhaar_payment",
            amount_paise=amount_paise,
            customer_id=customer_id,
            alert_id=alert_id,
            memo=memo or ("UPI udhaar repayment" if alert_id else "cash udhaar repayment"),
        ),
    )
    db.add(
        AuditLog(
            shop_id=shop_id,
            actor=actor,
            action="udhaar_payment",
            payload={"customer_id": customer_id, "amount_paise": amount_paise,
                     "entry_id": entry.id, "alert_id": alert_id},
        )
    )
    return entry
