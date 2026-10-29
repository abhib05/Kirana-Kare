"""Closing a basket: the one place a sale reaches the ledger and the stock count.

Called from the basket engine (cash / udhaar by voice), the reconciliation engine (UPI
match) and the dashboard (manual close). Flushes only — the caller's transaction makes
the status change, ledger posting, stock decrement and restock draft atomic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Basket, LedgerEntry
from app.inventory.service import on_basket_closed
from app.ledger import service as ledger

SALE_ENTRY = {"cash": "sale_cash", "upi": "sale_upi", "udhaar": "sale_udhaar"}


class BasketCloseError(ValueError):
    pass


def lock_basket(db: Session, basket_id: object) -> Basket | None:
    return db.execute(
        select(Basket).where(Basket.id == basket_id).with_for_update()
    ).scalar_one_or_none()


def close_basket(
    db: Session,
    basket: Basket,
    mode: str,
    *,
    customer_id: int | None = None,
    alert_id: int | None = None,
    amount_paise: int | None = None,
    actor: str = "system",
) -> LedgerEntry:
    """Close a locked basket as cash / upi / udhaar and post the sale."""
    if basket.status in ("closed", "cancelled"):
        raise BasketCloseError(f"basket {basket.id} is already {basket.status}")
    if mode not in SALE_ENTRY:
        raise BasketCloseError(f"unknown payment mode {mode!r}")
    amount = amount_paise if amount_paise is not None else basket.total_paise
    if amount <= 0:
        raise BasketCloseError("cannot close an empty basket")
    if customer_id is not None:
        basket.customer_id = customer_id
    if mode == "udhaar" and basket.customer_id is None:
        raise BasketCloseError("udhaar sale needs a customer")

    entry = ledger.post(
        db,
        ledger.Posting(
            shop_id=basket.shop_id,
            entry_type=SALE_ENTRY[mode],
            amount_paise=amount,
            customer_id=basket.customer_id,
            basket_id=basket.id,
            alert_id=alert_id,
            memo=(
                f"basket total {basket.total_paise}, received {amount}"
                if amount != basket.total_paise else None
            ),
        ),
    )
    basket.status = "closed"
    basket.payment_mode = mode
    basket.closed_at = datetime.now(UTC)
    on_basket_closed(db, basket)  # stock decrement + restock drafts, same transaction
    db.add(
        AuditLog(
            shop_id=basket.shop_id,
            actor=actor,
            action="basket_closed",
            payload={"basket_id": str(basket.id), "mode": mode, "amount_paise": amount,
                     "entry_id": entry.id, "alert_id": alert_id},
        )
    )
    db.flush()
    return entry
