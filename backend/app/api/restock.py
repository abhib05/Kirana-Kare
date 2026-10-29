from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.baskets import product_to_dict
from app.api.deps import get_db, shop_id
from app.db.models import Customer, Reminder, RestockDraft, Vendor
from app.inventory.service import InventoryError, receive_stock
from app.reminders.jobs import (
    ReminderError,
    remind_now,
    run_udhaar_reminders,
    set_reminder_status,
)
from app.restock.drafts import RestockError, edit_quantities, set_status

router = APIRouter(prefix="/api", tags=["restock", "reminders"])


class QtyIn(BaseModel):
    qty: Decimal = Field(gt=0)


class EditIn(BaseModel):
    quantities: dict[int, Decimal]


def draft_to_dict(d: RestockDraft, vendor: Vendor | None) -> dict[str, Any]:
    return {"id": d.id, "vendor_id": d.vendor_id, "vendor_name": vendor.name if vendor else None,
            "vendor_phone": vendor.phone if vendor else None, "items": d.items,
            "message_text": d.message_text, "wa_link": d.wa_link, "status": d.status,
            "created_at": d.created_at.isoformat()}


def reminder_to_dict(r: Reminder, name: str | None) -> dict[str, Any]:
    return {"id": r.id, "customer_id": r.customer_id, "customer_name": name,
            "amount_paise": r.amount_paise, "ref_id": r.ref_id, "pay_link": r.pay_link,
            "message_text": r.message_text, "wa_link": r.wa_link, "status": r.status,
            "created_at": r.created_at.isoformat()}


@router.get("/restock-drafts")
def list_drafts(status: str = "draft", db: Session = Depends(get_db),
                shop: int = Depends(shop_id)) -> list[dict[str, Any]]:
    rows = db.execute(
        select(RestockDraft, Vendor).join(Vendor, Vendor.id == RestockDraft.vendor_id)
        .where(RestockDraft.shop_id == shop, RestockDraft.status.in_(status.split(",")))
        .order_by(RestockDraft.created_at.desc())
    ).all()
    return [draft_to_dict(d, v) for d, v in rows]


def _draft_action(fn: Any, db: Session, shop: int, *args: Any) -> dict[str, Any]:
    try:
        d = fn(db, shop, *args)
    except RestockError as exc:
        raise HTTPException(409, str(exc)) from exc
    return draft_to_dict(d, db.get(Vendor, d.vendor_id))


@router.patch("/restock-drafts/{draft_id}")
def edit_draft(draft_id: int, body: EditIn, db: Session = Depends(get_db),
               shop: int = Depends(shop_id)) -> dict[str, Any]:
    return _draft_action(edit_quantities, db, shop, draft_id, body.quantities)


@router.post("/restock-drafts/{draft_id}/sent")
def mark_sent(draft_id: int, db: Session = Depends(get_db),
              shop: int = Depends(shop_id)) -> dict[str, Any]:
    return _draft_action(set_status, db, shop, draft_id, "sent")


@router.post("/restock-drafts/{draft_id}/dismiss")
def dismiss(draft_id: int, db: Session = Depends(get_db),
            shop: int = Depends(shop_id)) -> dict[str, Any]:
    return _draft_action(set_status, db, shop, draft_id, "dismissed")


@router.post("/products/{product_id}/receive")
def receive(product_id: int, body: QtyIn, db: Session = Depends(get_db),
            shop: int = Depends(shop_id)) -> dict[str, Any]:
    try:
        return product_to_dict(receive_stock(db, shop, product_id, body.qty))
    except InventoryError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/reminders")
def list_reminders(status: str = "draft", db: Session = Depends(get_db),
                   shop: int = Depends(shop_id)) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Reminder, Customer.display_name).join(Customer, Customer.id == Reminder.customer_id)
        .where(Reminder.shop_id == shop, Reminder.status.in_(status.split(",")))
        .order_by(Reminder.created_at.desc())
    ).all()
    return [reminder_to_dict(r, n) for r, n in rows]


@router.post("/reminders/run")
def run_reminders(db: Session = Depends(get_db),
                  shop: int = Depends(shop_id)) -> dict[str, Any]:
    made = run_udhaar_reminders(db, shop)
    return {"created": len(made)}


@router.post("/udhaar/{customer_id}/reminder")
def remind(customer_id: int, db: Session = Depends(get_db),
           shop: int = Depends(shop_id)) -> dict[str, Any]:
    try:
        r = remind_now(db, shop, customer_id)
    except ReminderError as exc:
        raise HTTPException(409, str(exc)) from exc
    c = db.get(Customer, customer_id)
    return reminder_to_dict(r, c.display_name if c else None)


@router.post("/reminders/{reminder_id}/{action}")
def reminder_action(reminder_id: int, action: str, db: Session = Depends(get_db),
                    shop: int = Depends(shop_id)) -> dict[str, Any]:
    status = {"sent": "sent", "dismiss": "dismissed"}.get(action)
    if status is None:
        raise HTTPException(404, "unknown action")
    try:
        r = set_reminder_status(db, shop, reminder_id, status)
    except ReminderError as exc:
        raise HTTPException(409, str(exc)) from exc
    c = db.get(Customer, r.customer_id)
    return reminder_to_dict(r, c.display_name if c else None)
