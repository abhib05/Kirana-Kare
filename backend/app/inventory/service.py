"""Stock bookkeeping. Runs inside the basket-close transaction, so a sale, its ledger
entry and its stock decrement commit (or roll back) together."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Basket, Product
from app.restock.drafts import refresh_vendor_draft


class InventoryError(ValueError):
    pass


def on_basket_closed(db: Session, basket: Basket) -> list[Product]:
    """Decrement stock for mapped lines; create/update restock drafts for the vendors of
    sold products that are at or below their reorder level. Returns those products."""
    by_product: dict[int, Decimal] = {}
    for item in basket.items:
        if item.product_id is None:
            continue
        by_product[item.product_id] = by_product.get(item.product_id, Decimal(0)) + item.qty
    if not by_product:
        return []

    products = db.execute(
        select(Product).where(Product.id.in_(by_product)).order_by(Product.id).with_for_update()
    ).scalars().all()
    low: list[Product] = []
    for p in products:
        item_units = {i.unit for i in basket.items if i.product_id == p.id}
        if item_units - {p.unit, None}:
            continue  # a line in a unit we couldn't convert: don't guess the stock change
        p.stock_qty = p.stock_qty - by_product[p.id]  # may go negative: stock was miscounted
        if p.stock_qty <= p.reorder_level:
            low.append(p)
    db.flush()

    for vendor_id in sorted({p.vendor_id for p in low if p.vendor_id is not None}):
        refresh_vendor_draft(db, basket.shop_id, vendor_id)
    return low


def receive_stock(db: Session, shop_id: int, product_id: int, qty: Decimal) -> Product:
    if qty <= 0:
        raise InventoryError("received quantity must be positive")
    p = db.execute(
        select(Product).where(Product.id == product_id).with_for_update()
    ).scalar_one_or_none()
    if p is None or p.shop_id != shop_id:
        raise InventoryError(f"product {product_id} not found")
    p.stock_qty = p.stock_qty + qty
    db.add(AuditLog(shop_id=shop_id, actor="merchant", action="stock_received",
                    payload={"product_id": product_id, "qty": str(qty)}))
    db.commit()
    return p
