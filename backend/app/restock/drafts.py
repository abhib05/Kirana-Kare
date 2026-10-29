"""Vendor restock drafts (§5.10): one open draft per vendor listing that vendor's low-stock
items with a suggested quantity, a polite Hindi/English message and a one-tap wa.me link."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import AuditLog, Product, RestockDraft, Shop, Vendor
from app.events import bus
from app.notify.whatsapp import wa_link

# A product in a draft that was sent this recently is "on order": don't ask again.
ON_ORDER_WINDOW = timedelta(days=2)


class RestockError(ValueError):
    pass


def suggested_qty(stock: Decimal, reorder_level: Decimal, multiplier: float) -> Decimal:
    """multiplier × reorder level − current stock, rounded up to a whole unit, at least 1."""
    target = reorder_level * Decimal(str(multiplier)) - stock
    return max(Decimal(1), target.quantize(Decimal(1), rounding=ROUND_CEILING))


def _fmt_qty(q: Decimal) -> str:
    return format(q.normalize(), "f")


def compose_message(shop_name: str, vendor_name: str, items: list[dict[str, Any]]) -> str:
    lines = "\n".join(f"- {i['name']}: {i['qty']} {i['unit']}" for i in items)
    return (
        f"Namaste {vendor_name} ji,\n"
        f"{shop_name} ke liye yeh saamaan bhej dijiye / please send:\n"
        f"{lines}\n"
        "Dhanyavaad! / Thank you!"
    )


def _on_order(db: Session, shop_id: int, vendor_id: int, now: datetime) -> set[int]:
    sent = db.execute(
        select(RestockDraft.items).where(
            RestockDraft.shop_id == shop_id, RestockDraft.vendor_id == vendor_id,
            RestockDraft.status == "sent", RestockDraft.created_at >= now - ON_ORDER_WINDOW,
        )
    ).scalars()
    return {int(i["product_id"]) for items in sent for i in items}


def refresh_vendor_draft(db: Session, shop_id: int, vendor_id: int,
                         settings: Settings | None = None,
                         now: datetime | None = None) -> RestockDraft | None:
    """Create or update the vendor's open draft from current low stock. Flushes only."""
    s = settings or get_settings()
    now = now or datetime.now(UTC)
    vendor = db.get(Vendor, vendor_id)
    shop = db.get(Shop, shop_id)
    if vendor is None or shop is None:
        return None
    on_order = _on_order(db, shop_id, vendor_id, now)
    low = db.execute(
        select(Product).where(
            Product.shop_id == shop_id, Product.vendor_id == vendor_id,
            Product.stock_qty <= Product.reorder_level,
        ).order_by(Product.name)
    ).scalars().all()
    draft = db.execute(
        select(RestockDraft).where(
            RestockDraft.shop_id == shop_id, RestockDraft.vendor_id == vendor_id,
            RestockDraft.status == "draft",
        ).with_for_update()
    ).scalar_one_or_none()

    # Keep quantities the merchant already edited on the open draft.
    edited = {int(i["product_id"]): i for i in (draft.items if draft else []) if i.get("edited")}
    items = []
    for p in low:
        if p.id in on_order:
            continue
        if p.id in edited:
            items.append(edited[p.id])
            continue
        qty = suggested_qty(p.stock_qty, p.reorder_level, s.restock_multiplier)
        items.append({"product_id": p.id, "name": p.name, "unit": p.unit, "qty": _fmt_qty(qty),
                      "stock": _fmt_qty(p.stock_qty), "edited": False})
    if not items:
        return draft

    message = compose_message(shop.name, vendor.name, items)
    if draft is None:
        draft = RestockDraft(shop_id=shop_id, vendor_id=vendor_id, items=items,
                             message_text=message, wa_link=wa_link(vendor.phone, message),
                             status="draft")
        db.add(draft)
    else:
        draft.items = items
        draft.message_text = message
        draft.wa_link = wa_link(vendor.phone, message)
    db.flush()
    bus.publish("restock_draft", {"draft_id": draft.id, "vendor": vendor.name,
                                  "items": len(items)})
    return draft


def edit_quantities(db: Session, shop_id: int, draft_id: int,
                    quantities: dict[int, Decimal]) -> RestockDraft:
    draft = _get_open(db, shop_id, draft_id)
    vendor = db.get(Vendor, draft.vendor_id)
    shop = db.get(Shop, shop_id)
    assert vendor is not None and shop is not None
    items = []
    for i in draft.items:
        pid = int(i["product_id"])
        if pid in quantities:
            q = quantities[pid]
            if q < 0:
                raise RestockError("quantity cannot be negative")
            if q == 0:
                continue  # merchant removed the line
            i = {**i, "qty": _fmt_qty(q), "edited": True}
        items.append(i)
    if not items:
        raise RestockError("a draft needs at least one item; dismiss it instead")
    draft.items = items
    draft.message_text = compose_message(shop.name, vendor.name, items)
    draft.wa_link = wa_link(vendor.phone, draft.message_text)
    db.commit()
    return draft


def set_status(db: Session, shop_id: int, draft_id: int, status: str) -> RestockDraft:
    draft = _get_open(db, shop_id, draft_id)
    draft.status = status
    db.add(AuditLog(shop_id=shop_id, actor="merchant", action=f"restock_{status}",
                    payload={"draft_id": draft_id, "items": draft.items}))
    db.commit()
    return draft


def _get_open(db: Session, shop_id: int, draft_id: int) -> RestockDraft:
    draft = db.get(RestockDraft, draft_id)
    if draft is None or draft.shop_id != shop_id:
        raise RestockError(f"draft {draft_id} not found")
    if draft.status != "draft":
        raise RestockError(f"draft {draft_id} is already {draft.status}")
    return draft
