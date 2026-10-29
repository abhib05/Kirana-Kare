import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, shop_id
from app.api.schemas import AlertOut
from app.db.models import Basket, Customer, UpiAlert
from app.reconciliation.engine import MerchantChoice, ReconciliationError, merchant_decide

router = APIRouter(prefix="/api", tags=["reconciliation"])


class DecideIn(BaseModel):
    choice: MerchantChoice
    basket_id: uuid.UUID | None = None
    customer_id: int | None = None


def _basket_summary(db: Session, basket_id: str) -> dict[str, Any] | None:
    b = db.get(Basket, uuid.UUID(basket_id))
    if b is None:
        return None
    return {
        "status": b.status,
        "items": [f"{i.qty.normalize()} {i.unit or ''} {i.product_hint or i.raw_text}".strip()
                  for i in b.items],
        "customer_name": b.customer_name_hint,
        "upi_ref_id": b.upi_ref_id,
    }


@router.get("/confirm-queue")
def confirm_queue(db: Session = Depends(get_db), shop: int = Depends(shop_id)) -> list[dict]:
    alerts = db.execute(
        select(UpiAlert)
        .where(UpiAlert.shop_id == shop, UpiAlert.status.in_(("unmatched", "pending_confirm")))
        .order_by(UpiAlert.received_at.desc())
    ).scalars().all()
    names = dict(db.execute(
        select(Customer.id, Customer.display_name).where(Customer.shop_id == shop)
    ).all())
    out = []
    for a in alerts:
        decision = a.candidates or {}
        candidates = []
        for c in decision.get("candidates", []):
            candidates.append({
                **c,
                "basket": _basket_summary(db, c["basket_id"]),
                "customer_name": names.get(c.get("customer_id")),
            })
        suggested = decision.get("suggested_customer_id")
        out.append({
            "alert": AlertOut.model_validate(a).model_dump(mode="json", exclude={"candidates"}),
            "candidates": candidates,
            "reason": decision.get("reason"),
            "suggested_customer": (
                {"id": suggested, "display_name": names.get(suggested)} if suggested else None
            ),
        })
    return out


@router.post("/matches/{alert_id}/decide", response_model=AlertOut)
def decide_alert(
    alert_id: int, body: DecideIn, db: Session = Depends(get_db), shop: int = Depends(shop_id)
) -> AlertOut:
    try:
        alert = merchant_decide(db, shop, alert_id, body.choice, basket_id=body.basket_id,
                                customer_id=body.customer_id)
    except ReconciliationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return AlertOut.model_validate(alert)
