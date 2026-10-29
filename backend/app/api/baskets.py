import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, shop_id
from app.baskets.closing import BasketCloseError
from app.baskets.engine import BasketEngine, basket_to_dict
from app.db.models import Basket, Product, Vendor
from app.extraction.extractor import Extractor, get_extractor
from app.extraction.schemas import Turn
from app.redis_client import get_redis

router = APIRouter(prefix="/api", tags=["baskets"])


def get_engine_deps() -> Extractor:
    return get_extractor()


def engine(
    db: Session = Depends(get_db), redis: Redis = Depends(get_redis),
    shop: int = Depends(shop_id), extractor: Extractor = Depends(get_engine_deps),
) -> BasketEngine:
    return BasketEngine(db, redis, shop, extractor)


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    speaker: str | None = None
    turn_id: str | None = None


class CloseIn(BaseModel):
    mode: Literal["cash", "upi", "udhaar"]
    customer_id: int | None = None


class MapIn(BaseModel):
    product_id: int


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = []
    unit: Literal["kg", "g", "l", "ml", "pcs", "packet", "dozen"]
    price_paise: int = Field(gt=0)
    stock_qty: Decimal = Decimal(0)
    reorder_level: Decimal = Decimal(0)
    vendor_id: int | None = None


class VendorIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(pattern=r"^\d{10,15}$")


@router.post("/dev/turn")
def dev_turn(body: TurnIn, eng: BasketEngine = Depends(engine)) -> dict[str, Any]:
    """Drive the pipeline with typed text, exactly as a final ASR turn would."""
    turn = Turn(turn_id=body.turn_id or f"dev-{uuid.uuid4().hex[:8]}", text=body.text,
                ts=datetime.now(UTC), speaker=body.speaker)
    return eng.process_turn(turn).as_dict()


@router.get("/baskets")
def list_baskets(
    status: str | None = None, limit: int = 50, db: Session = Depends(get_db),
    shop: int = Depends(shop_id),
) -> list[dict[str, Any]]:
    q = select(Basket).where(Basket.shop_id == shop)
    if status:
        q = q.where(Basket.status.in_(status.split(",")))
    q = q.order_by(Basket.opened_at.desc()).limit(min(limit, 200))
    return [basket_to_dict(b) for b in db.execute(q).scalars()]


@router.get("/baskets/{basket_id}")
def get_basket(basket_id: uuid.UUID, db: Session = Depends(get_db),
               shop: int = Depends(shop_id)) -> dict[str, Any]:
    b = db.get(Basket, basket_id)
    if b is None or b.shop_id != shop:
        raise HTTPException(404, "basket not found")
    return basket_to_dict(b)


@router.post("/baskets/{basket_id}/close")
def close(basket_id: uuid.UUID, body: CloseIn,
          eng: BasketEngine = Depends(engine)) -> dict[str, Any]:
    try:
        return basket_to_dict(eng.close_manually(basket_id, body.mode, body.customer_id))
    except BasketCloseError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/baskets/{basket_id}/cancel")
def cancel(basket_id: uuid.UUID, eng: BasketEngine = Depends(engine)) -> dict[str, Any]:
    try:
        return basket_to_dict(eng.cancel(basket_id))
    except BasketCloseError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/basket-items/{item_id}/map")
def map_item(item_id: int, body: MapIn, eng: BasketEngine = Depends(engine)) -> dict[str, Any]:
    try:
        item = eng.map_item(item_id, body.product_id)
    except BasketCloseError as exc:
        raise HTTPException(409, str(exc)) from exc
    return basket_to_dict(item.basket)


def product_to_dict(p: Product) -> dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "aliases": p.aliases, "unit": p.unit,
        "price_paise": p.price_paise, "stock_qty": str(p.stock_qty.normalize()),
        "reorder_level": str(p.reorder_level.normalize()), "vendor_id": p.vendor_id,
        "low_stock": p.stock_qty <= p.reorder_level,
    }


@router.get("/products")
def products(db: Session = Depends(get_db), shop: int = Depends(shop_id)) -> list[dict]:
    rows = db.execute(select(Product).where(Product.shop_id == shop).order_by(Product.name))
    return [product_to_dict(p) for p in rows.scalars()]


@router.post("/products")
def create_product(body: ProductIn, db: Session = Depends(get_db),
                   shop: int = Depends(shop_id)) -> dict[str, Any]:
    p = Product(shop_id=shop, **body.model_dump())
    db.add(p)
    db.commit()
    return product_to_dict(p)


@router.get("/vendors")
def vendors(db: Session = Depends(get_db), shop: int = Depends(shop_id)) -> list[dict]:
    rows = db.execute(select(Vendor).where(Vendor.shop_id == shop).order_by(Vendor.name))
    return [{"id": v.id, "name": v.name, "phone": v.phone} for v in rows.scalars()]


@router.post("/vendors")
def create_vendor(body: VendorIn, db: Session = Depends(get_db),
                  shop: int = Depends(shop_id)) -> dict[str, Any]:
    v = Vendor(shop_id=shop, name=body.name, phone=body.phone)
    db.add(v)
    db.commit()
    return {"id": v.id, "name": v.name, "phone": v.phone}
