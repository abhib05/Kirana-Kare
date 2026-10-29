"""Basket session engine (§5.4).

Postgres is the source of truth for baskets and items. Redis holds the live session state:
  basket:{shop}:{id}     hash  status / total / customer / last activity (dashboard cache)
  open_baskets:{shop}    zset  open basket ids scored by opened_at
  turns:{shop}           list  last N final turns, the extractor's rolling context
  lock:baskets:{shop}    lock  serializes turn processing per shop

One open basket per conversation window: a transactional turn appends to it or opens one.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.baskets.closing import BasketCloseError, close_basket, lock_basket
from app.config import Settings, get_settings
from app.customers.resolve import Resolution, add_voice_alias, resolve_customer
from app.db.models import AuditLog, Basket, BasketItem, Product, Shop
from app.events import bus
from app.extraction.catalog_match import CatalogEntry, convert_qty, match_product
from app.extraction.extractor import Extractor
from app.extraction.schemas import (
    ExtractedItem,
    ExtractedTurn,
    ExtractionRejected,
    Turn,
    check_price_band,
)
from app.money import line_total
from app.reconciliation.engine import rereconcile_pending
from app.udhaar.service import UdhaarError, record_payment
from app.upi.links import new_ref_id, upi_pay_link

log = logging.getLogger(__name__)

CONTEXT_TURNS = 6
RECENT_AWAITING = timedelta(minutes=15)


@dataclass
class TurnResult:
    status: str  # 'ignored' | 'rejected' | 'applied'
    actions: list[str] = field(default_factory=list)
    basket: dict[str, Any] | None = None
    reason: str | None = None
    extracted: dict[str, Any] | None = None
    needs_customer: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "actions": self.actions, "basket": self.basket,
            "reason": self.reason, "extracted": self.extracted,
            "needs_customer": self.needs_customer,
        }


@dataclass
class _PlannedLine:
    item: ExtractedItem
    product: CatalogEntry | None
    score: float
    flagged: bool
    qty: Decimal
    unit: str | None
    unit_price: int
    line: int


def basket_to_dict(b: Basket) -> dict[str, Any]:
    return {
        "id": str(b.id),
        "status": b.status,
        "payment_mode": b.payment_mode,
        "total_paise": b.total_paise,
        "customer_id": b.customer_id,
        "customer_name": b.customer_name_hint,
        "upi_ref_id": b.upi_ref_id,
        "upi_link": b.upi_link,
        "opened_at": b.opened_at.isoformat() if b.opened_at else None,
        "awaiting_since": b.awaiting_since.isoformat() if b.awaiting_since else None,
        "closed_at": b.closed_at.isoformat() if b.closed_at else None,
        "items": [
            {
                "id": i.id, "product_id": i.product_id, "raw_text": i.raw_text,
                "product_hint": i.product_hint, "qty": str(i.qty.normalize()), "unit": i.unit,
                "unit_price_paise": i.unit_price_paise, "line_total_paise": i.line_total_paise,
                "match_score": i.match_score, "flagged": i.flagged,
            }
            for i in b.items
        ],
    }


def load_catalog(db: Session, shop_id: int) -> list[CatalogEntry]:
    """Products ordered by how often they sell, so the prompt's top-N is the useful N."""
    sold = (
        select(BasketItem.product_id, func.count().label("n"))
        .where(BasketItem.shop_id == shop_id)
        .group_by(BasketItem.product_id)
        .subquery()
    )
    rows = db.execute(
        select(Product)
        .outerjoin(sold, sold.c.product_id == Product.id)
        .where(Product.shop_id == shop_id)
        .order_by(func.coalesce(sold.c.n, 0).desc(), Product.name)
    ).scalars()
    return [CatalogEntry(p.id, p.name, tuple(p.aliases or ()), p.unit, p.price_paise)
            for p in rows]


class BasketEngine:
    def __init__(self, db: Session, redis: Redis, shop_id: int, extractor: Extractor,
                 settings: Settings | None = None) -> None:
        self.db = db
        self.redis = redis
        self.shop_id = shop_id
        self.extractor = extractor
        self.settings = settings or get_settings()

    # ---- redis state ---------------------------------------------------------------------
    @property
    def _open_key(self) -> str:
        return f"open_baskets:{self.shop_id}"

    def _basket_key(self, basket_id: uuid.UUID | str) -> str:
        return f"basket:{self.shop_id}:{basket_id}"

    @property
    def _turns_key(self) -> str:
        return f"turns:{self.shop_id}"

    def context(self) -> list[Turn]:
        out = []
        for raw in self.redis.lrange(self._turns_key, 0, -1):  # type: ignore[union-attr]
            d = json.loads(raw)
            out.append(Turn(d["turn_id"], d["text"], datetime.fromisoformat(d["ts"]),
                            d.get("speaker"), d.get("is_merchant")))
        return out

    def _remember(self, turn: Turn) -> None:
        self.redis.rpush(self._turns_key, json.dumps({
            "turn_id": turn.turn_id, "text": turn.text, "ts": turn.ts.isoformat(),
            "speaker": turn.speaker, "is_merchant": turn.is_merchant,
        }))
        self.redis.ltrim(self._turns_key, -CONTEXT_TURNS, -1)

    def sync_cache(self, b: Basket) -> None:
        key = self._basket_key(b.id)
        self.redis.hset(key, mapping={
            "status": b.status, "total_paise": b.total_paise,
            "customer_id": b.customer_id or "", "payment_mode": b.payment_mode or "",
            "last_activity": (b.last_activity_at or b.opened_at).isoformat(),
            "items": len(b.items),
        })
        self.redis.expire(key, 86400)
        if b.status == "open":
            self.redis.zadd(self._open_key, {str(b.id): b.opened_at.timestamp()})
        else:
            self.redis.zrem(self._open_key, str(b.id))

    def current_open(self) -> Basket | None:
        ids: list[str] = self.redis.zrevrange(self._open_key, 0, -1)  # type: ignore[assignment]
        for bid in ids:
            b = self.db.get(Basket, uuid.UUID(bid))
            if b is not None and b.status == "open":
                return b
            self.redis.zrem(self._open_key, bid)
        return None

    def _recent_awaiting(self, now: datetime) -> Basket | None:
        return self.db.execute(
            select(Basket).where(
                Basket.shop_id == self.shop_id, Basket.status == "awaiting_payment",
                Basket.awaiting_since >= now - RECENT_AWAITING,
            ).order_by(Basket.awaiting_since.desc()).limit(1)
        ).scalar_one_or_none()

    # ---- turn processing -------------------------------------------------------------------
    def process_turn(self, turn: Turn) -> TurnResult:
        with self.redis.lock(f"lock:baskets:{self.shop_id}", timeout=30, blocking_timeout=15):
            context = self.context()
            self._remember(turn)
            try:
                result = self._process(turn, context)
            except ExtractionRejected as exc:
                self.db.rollback()
                self._log_rejection(turn, exc)
                return TurnResult("rejected", reason=exc.reason)
        bus.publish("turn", {"turn_id": turn.turn_id, "text": turn.text,
                             "speaker": turn.speaker, **result.as_dict()})
        return result

    def _log_rejection(self, turn: Turn, exc: ExtractionRejected) -> None:
        log.warning("extraction rejected for turn %s: %s", turn.turn_id, exc.reason)
        self.db.add(AuditLog(shop_id=self.shop_id, actor="extractor",
                             action="extraction_rejected",
                             payload={"turn_id": turn.turn_id, "text": turn.text,
                                      "reason": exc.reason,
                                      "raw": None if exc.raw is None else str(exc.raw)[:2000]}))
        self.db.commit()
        bus.publish("extraction_rejected", {"turn_id": turn.turn_id, "text": turn.text,
                                            "reason": exc.reason})

    def _process(self, turn: Turn, context: list[Turn]) -> TurnResult:
        catalog = load_catalog(self.db, self.shop_id)
        ext = self.extractor.extract(turn, context, catalog)
        extracted = ext.model_dump(mode="json")
        if not ext.is_transactional or (
            ext.action == "none" and not ext.items and ext.payment_mode == "unknown"
        ):
            return TurnResult("ignored", extracted=extracted)

        if ext.action == "udhaar_payment":
            return self._udhaar_payment(ext, extracted)

        now = turn.ts
        planned = self._plan_lines(ext.items, catalog) if ext.items else []
        actions: list[str] = []
        basket = self.current_open()

        if planned and ext.action != "remove_items":
            if basket is None:
                basket = self._open(now)
                actions.append("opened")
            self._add_lines(basket, planned, turn)
            actions.append(f"added {len(planned)}")
        elif planned and ext.action == "remove_items" and basket is not None:
            removed = self._remove_lines(basket, planned)
            actions.append(f"removed {removed}")

        if basket is None and (ext.payment_mode != "unknown" or ext.action == "close_basket"):
            basket = self._recent_awaiting(now)
        if basket is None:
            self.db.rollback()
            return TurnResult("ignored", reason="no basket to apply this turn to",
                              extracted=extracted)

        resolution = self._apply_customer(basket, ext.customer_name)
        basket.last_activity_at = now
        basket.source_turn_ids = [*(basket.source_turn_ids or []), turn.turn_id]
        self._recompute_total(basket)
        result = TurnResult("applied", actions=actions, extracted=extracted)
        self._apply_payment(basket, ext, now, result, resolution)
        self.db.commit()
        self.sync_cache(basket)
        result.basket = basket_to_dict(basket)
        bus.publish("basket_closed" if basket.status == "closed" else "basket_updated",
                    result.basket)
        if basket.status == "awaiting_payment" and basket.payment_mode == "upi":
            rereconcile_pending(self.db, self.shop_id, basket, self.settings)
        return result

    def _plan_lines(self, items: list[ExtractedItem], catalog: list[CatalogEntry]
                    ) -> list[_PlannedLine]:
        """Match and price every line before writing anything: one bad line rejects the turn."""
        planned = []
        for it in items:
            m = match_product(it.product_hint, catalog)
            if m.product is None:
                line = line_total(it.qty, it.spoken_price_paise) if it.spoken_price_paise else 0
                planned.append(_PlannedLine(it, None, m.score, True, it.qty, it.unit,
                                            it.spoken_price_paise or 0, line))
                continue
            p = m.product
            qty, compatible = convert_qty(it.qty, it.unit, p.unit)
            if it.spoken_price_paise is not None:
                line = line_total(it.qty, it.spoken_price_paise)
                if compatible and qty > 0:
                    check_price_band(it.product_hint, Decimal(line) / qty, p.price_paise)
                unit_price = int((Decimal(line) / qty).to_integral_value()) if qty else 0
            elif compatible:
                line, unit_price = line_total(qty, p.price_paise), p.price_paise
            else:  # "500 gram jeera" against a per-packet SKU: can't price it, ask the merchant
                line, unit_price = 0, 0
            planned.append(_PlannedLine(it, p, m.score, m.flagged or not compatible,
                                        qty if compatible else it.qty,
                                        p.unit if compatible else it.unit, unit_price, line))
        return planned

    def _open(self, now: datetime) -> Basket:
        b = Basket(id=uuid.uuid4(), shop_id=self.shop_id, status="open", total_paise=0,
                   opened_at=now, last_activity_at=now, source_turn_ids=[])
        self.db.add(b)
        self.db.flush()
        return b

    def _add_lines(self, basket: Basket, planned: list[_PlannedLine], turn: Turn) -> None:
        for pl in planned:
            basket.items.append(BasketItem(
                shop_id=self.shop_id, product_id=pl.product.id if pl.product else None,
                raw_text=pl.item.raw_text or pl.item.product_hint,
                product_hint=pl.item.product_hint, qty=pl.qty, unit=pl.unit,
                unit_price_paise=pl.unit_price, line_total_paise=pl.line,
                match_score=pl.score, flagged=pl.flagged,
                prompt_version=self.extractor.prompt_version,
            ))
        self.db.flush()

    def _remove_lines(self, basket: Basket, planned: list[_PlannedLine]) -> int:
        removed = 0
        for pl in planned:
            for existing in reversed(basket.items):
                same = (pl.product is not None and existing.product_id == pl.product.id) or (
                    pl.product is None and existing.product_hint == pl.item.product_hint)
                if same:
                    basket.items.remove(existing)
                    removed += 1
                    break
        self.db.flush()
        return removed

    def _recompute_total(self, basket: Basket) -> None:
        basket.total_paise = sum(i.line_total_paise for i in basket.items)

    def _apply_customer(self, basket: Basket, spoken: str | None) -> Resolution | None:
        if not spoken:
            return None
        basket.customer_name_hint = spoken
        res = resolve_customer(self.db, self.shop_id, spoken)
        if res.customer_id is not None:
            basket.customer_id = res.customer_id
        return res

    def _apply_payment(self, basket: Basket, ext: ExtractedTurn, now: datetime,
                       result: TurnResult, resolution: Resolution | None) -> None:
        mode: str = ext.payment_mode
        if mode == "unknown" and ext.action != "close_basket":
            return
        if basket.total_paise <= 0:
            result.actions.append("payment ignored: empty basket")
            return
        if mode == "unknown":
            mode = basket.payment_mode or "unknown"  # intent stated earlier ("cash dunga")

        if mode == "cash" and ext.action != "close_basket" and basket.status == "open":
            basket.payment_mode = "cash"  # remember intent; close on "bas"/"ho gaya"
            result.actions.append("cash intent noted")
            return
        if mode in ("cash", "udhaar"):
            if mode == "udhaar" and basket.customer_id is None:
                self._await(basket, now, "udhaar")
                cands = resolution.candidates if resolution else []
                result.needs_customer = {
                    "spoken_name": basket.customer_name_hint,
                    "candidates": [{"id": c[0], "display_name": c[1], "score": c[2]}
                                   for c in cands],
                }
                result.actions.append("udhaar: customer needed")
                return
            locked = lock_basket(self.db, basket.id)
            assert locked is not None
            close_basket(self.db, locked, mode, actor="voice")
            result.actions.append(f"closed {mode}")
            return
        if mode == "upi":
            self._await(basket, now, "upi")
            self._assign_upi_ref(basket)
            result.actions.append("awaiting upi")
            return
        self._await(basket, now, None)
        result.actions.append("awaiting payment")

    def _await(self, basket: Basket, now: datetime, mode: str | None) -> None:
        basket.status = "awaiting_payment"
        basket.payment_mode = mode
        basket.awaiting_since = now

    def _assign_upi_ref(self, basket: Basket) -> None:
        if basket.upi_ref_id:
            return
        shop = self.db.get(Shop, self.shop_id)
        assert shop is not None
        for _ in range(5):
            ref = new_ref_id()
            try:
                with self.db.begin_nested():
                    basket.upi_ref_id = ref
                    self.db.flush()
                break
            except IntegrityError:
                continue
        assert basket.upi_ref_id
        basket.upi_link = upi_pay_link(shop.upi_vpa, shop.name, basket.total_paise,
                                       basket.upi_ref_id, f"Kirana Kare {basket.upi_ref_id}")

    def _udhaar_payment(self, ext: ExtractedTurn, extracted: dict[str, Any]) -> TurnResult:
        assert ext.udhaar_payment_paise is not None
        res = resolve_customer(self.db, self.shop_id, ext.customer_name)
        if ext.payment_mode == "upi":
            # The money arrives as a UPI alert; reconciliation will suggest it as repayment.
            return TurnResult("applied", actions=["udhaar repayment via UPI: awaiting alert"],
                              extracted=extracted)
        if res.customer_id is None:
            return TurnResult(
                "applied", actions=["udhaar repayment: customer needed"], extracted=extracted,
                needs_customer={
                    "spoken_name": ext.customer_name,
                    "amount_paise": ext.udhaar_payment_paise,
                    "candidates": [{"id": c[0], "display_name": c[1], "score": c[2]}
                                   for c in res.candidates],
                })
        try:
            entry = record_payment(self.db, self.shop_id, res.customer_id,
                                   ext.udhaar_payment_paise, actor="voice")
        except UdhaarError as exc:
            raise ExtractionRejected(str(exc)) from exc
        self.db.commit()
        bus.publish("udhaar_payment", {"customer_id": res.customer_id,
                                       "amount_paise": ext.udhaar_payment_paise,
                                       "entry_id": entry.id})
        return TurnResult("applied", actions=[f"udhaar repayment {ext.udhaar_payment_paise}"],
                          extracted=extracted)

    # ---- manual operations (dashboard) -----------------------------------------------------
    def close_manually(self, basket_id: uuid.UUID, mode: str,
                       customer_id: int | None = None) -> Basket:
        b = lock_basket(self.db, basket_id)
        if b is None or b.shop_id != self.shop_id:
            raise BasketCloseError(f"basket {basket_id} not found")
        if mode == "upi":
            now = datetime.now(UTC)
            if b.status == "open":
                self._await(b, now, "upi")
            b.payment_mode = "upi"
            self._assign_upi_ref(b)
        else:
            close_basket(self.db, b, mode, customer_id=customer_id, actor="merchant")
            if customer_id is not None and mode == "udhaar" and b.customer_name_hint:
                add_voice_alias(self.db, self.shop_id, customer_id, b.customer_name_hint)
        self.db.commit()
        self.sync_cache(b)
        bus.publish("basket_closed" if b.status == "closed" else "basket_updated",
                    basket_to_dict(b))
        if b.status == "awaiting_payment":
            rereconcile_pending(self.db, self.shop_id, b, self.settings)
        return b

    def cancel(self, basket_id: uuid.UUID) -> Basket:
        b = lock_basket(self.db, basket_id)
        if b is None or b.shop_id != self.shop_id:
            raise BasketCloseError(f"basket {basket_id} not found")
        if b.status == "closed":
            raise BasketCloseError("closed baskets are corrected with a ledger reversal")
        b.status = "cancelled"
        b.closed_at = datetime.now(UTC)
        self.db.add(AuditLog(shop_id=self.shop_id, actor="merchant", action="basket_cancelled",
                             payload={"basket_id": str(b.id)}))
        self.db.commit()
        self.sync_cache(b)
        bus.publish("basket_updated", basket_to_dict(b))
        return b

    def map_item(self, item_id: int, product_id: int) -> BasketItem:
        """One-tap mapping of an unmapped/flagged line; the spoken hint becomes an alias."""
        item = self.db.get(BasketItem, item_id)
        product = self.db.get(Product, product_id)
        if item is None or product is None or product.shop_id != self.shop_id:
            raise BasketCloseError("item or product not found")
        basket = item.basket
        if basket.status in ("closed", "cancelled"):
            raise BasketCloseError("cannot edit a finished basket")
        qty, compatible = convert_qty(item.qty, item.unit, product.unit)
        item.product_id = product.id
        item.flagged = False
        item.match_score = 100.0
        if compatible:
            item.qty, item.unit = qty, product.unit
            if item.unit_price_paise == 0:
                item.unit_price_paise = product.price_paise
                item.line_total_paise = line_total(qty, product.price_paise)
        hint = (item.product_hint or "").strip()
        if hint and hint not in [a.lower() for a in product.aliases] and \
                hint != product.name.lower():
            product.aliases = [*product.aliases, hint]
        self._recompute_total(basket)
        self.db.commit()
        self.sync_cache(basket)
        bus.publish("basket_updated", basket_to_dict(basket))
        return item

    def sweep_idle(self, now: datetime | None = None) -> list[Basket]:
        """Open baskets idle past the timeout move to awaiting_payment for the dashboard."""
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.settings.basket_idle_timeout_seconds)
        swept = []
        for b in self.db.execute(
            select(Basket).where(Basket.shop_id == self.shop_id, Basket.status == "open")
        ).scalars():
            last = b.last_activity_at or b.opened_at
            if last <= cutoff:
                if b.total_paise <= 0:
                    b.status = "cancelled"
                else:
                    self._await(b, now, b.payment_mode)
                swept.append(b)
        self.db.commit()
        for b in swept:
            self.sync_cache(b)
            bus.publish("basket_updated", basket_to_dict(b))
            if b.status == "awaiting_payment":
                rereconcile_pending(self.db, self.shop_id, b, self.settings)
        return swept
