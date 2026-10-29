from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.alerts.sms_parser import IST
from app.api.deps import get_db, shop_id
from app.api.schemas import LedgerEntryOut
from app.db.models import AuditLog, Basket, Customer, Match, UpiAlert
from app.events import bus
from app.ledger import service as ledger
from app.udhaar.service import UdhaarError, record_payment

router = APIRouter(prefix="/api", tags=["ledger"])


class PaymentIn(BaseModel):
    amount_paise: int = Field(gt=0)
    memo: str | None = None


class ReverseIn(BaseModel):
    memo: str | None = None


@router.get("/customers")
def customers(db: Session = Depends(get_db), shop: int = Depends(shop_id)) -> list[dict]:
    rows = db.execute(
        select(Customer).where(Customer.shop_id == shop).order_by(Customer.display_name)
    ).scalars()
    return [
        {"id": c.id, "display_name": c.display_name, "phone": c.phone,
         "aliases": [a.alias for a in c.aliases]}
        for c in rows
    ]


@router.get("/udhaar")
def udhaar_balances(db: Session = Depends(get_db), shop: int = Depends(shop_id)) -> list[dict]:
    return ledger.all_balances(db, shop)


@router.post("/udhaar/{customer_id}/payment", response_model=LedgerEntryOut)
def udhaar_payment(
    customer_id: int, body: PaymentIn, db: Session = Depends(get_db),
    shop: int = Depends(shop_id),
) -> LedgerEntryOut:
    try:
        entry = record_payment(db, shop, customer_id, body.amount_paise, memo=body.memo)
    except UdhaarError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    out = LedgerEntryOut.model_validate(entry)
    bus.publish("ledger_updated", out.model_dump(mode="json"))
    return out


@router.get("/summary")
def summary(
    day: date | None = Query(default=None, alias="date"), db: Session = Depends(get_db),
    shop: int = Depends(shop_id),
) -> dict[str, Any]:
    """Cash / UPI / udhaar totals for one IST day, plus alert and basket counts."""
    d = day or datetime.now(IST).date()
    row = db.execute(
        text("SELECT cash_paise, upi_paise, udhaar_paise, udhaar_collected_paise "
             "FROM daily_summary WHERE shop_id = :s AND day = :d"), {"s": shop, "d": d}
    ).mappings().first()
    totals = dict(row) if row else {"cash_paise": 0, "upi_paise": 0, "udhaar_paise": 0,
                                    "udhaar_collected_paise": 0}
    start = datetime.combine(d, time.min, tzinfo=IST)
    end = start + timedelta(days=1)
    alerts = dict(db.execute(
        select(UpiAlert.status, func.count()).where(
            UpiAlert.shop_id == shop, UpiAlert.received_at >= start, UpiAlert.received_at < end
        ).group_by(UpiAlert.status)
    ).all())
    auto = db.scalar(select(func.count()).select_from(Match).where(
        Match.shop_id == shop, Match.decided_by == "auto", Match.decided_at >= start,
        Match.decided_at < end))
    baskets = dict(db.execute(
        select(Basket.status, func.count()).where(Basket.shop_id == shop,
                                                  Basket.opened_at >= start,
                                                  Basket.opened_at < end)
        .group_by(Basket.status)
    ).all())
    total_sales = totals["cash_paise"] + totals["upi_paise"] + totals["udhaar_paise"]
    return {
        "date": d.isoformat(), **totals, "total_sales_paise": total_sales,
        "alerts": {"matched": alerts.get("matched", 0),
                   "pending": alerts.get("pending_confirm", 0) + alerts.get("unmatched", 0),
                   "ignored": alerts.get("ignored", 0), "auto_matched": auto or 0},
        "baskets": baskets,
    }


@router.get("/ledger", response_model=list[LedgerEntryOut])
def ledger_entries(
    limit: int = 100, db: Session = Depends(get_db), shop: int = Depends(shop_id)
) -> list[LedgerEntryOut]:
    return [LedgerEntryOut.model_validate(e) for e in ledger.entries(db, shop, limit)]


@router.post("/ledger/{entry_id}/reverse", response_model=LedgerEntryOut)
def reverse_entry(
    entry_id: int, body: ReverseIn, db: Session = Depends(get_db),
    shop: int = Depends(shop_id),
) -> LedgerEntryOut:
    try:
        entry = ledger.reverse(db, shop, entry_id, body.memo)
    except ledger.LedgerError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.add(AuditLog(shop_id=shop, actor="merchant", action="ledger_reversal",
                    payload={"reversed_entry_id": entry_id, "entry_id": entry.id}))
    db.commit()
    out = LedgerEntryOut.model_validate(entry)
    bus.publish("ledger_updated", out.model_dump(mode="json"))
    return out
