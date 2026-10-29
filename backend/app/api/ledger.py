from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, shop_id
from app.api.schemas import LedgerEntryOut
from app.db.models import AuditLog, Customer
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
    return LedgerEntryOut.model_validate(entry)


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
    return LedgerEntryOut.model_validate(entry)
