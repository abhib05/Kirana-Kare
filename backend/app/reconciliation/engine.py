"""Reconciliation engine: load candidates, score them (scoring.py), apply the decision.

Exactly-once guarantees
- The alert row is locked (SELECT … FOR UPDATE) for the whole decision, so two workers
  can never decide the same alert concurrently; an alert already matched/ignored is
  left alone.
- The chosen basket row is locked too, and re-checked: if another alert closed it in the
  meantime this alert falls back to the confirm queue instead of double-closing it.
- `matches.alert_id` is UNIQUE as a final backstop.
- The match row, basket close, ledger posting and audit entry commit in one transaction.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Literal

from rapidfuzz import fuzz, utils
from sqlalchemy import ColumnElement, func, or_, select, text
from sqlalchemy.orm import Session

from app.baskets.closing import BasketCloseError, close_basket, lock_basket
from app.config import Settings, get_settings
from app.db.models import AuditLog, Basket, Customer, CustomerAlias, Match, Reminder, UpiAlert
from app.events import bus
from app.reconciliation.scoring import (
    AlertView,
    BasketView,
    CustomerView,
    Decision,
    decide,
)
from app.udhaar.service import record_payment

CANDIDATE_STATUSES = ("open", "awaiting_payment")


class ReconciliationError(ValueError):
    pass


def _lock_alert(db: Session, shop_id: int, alert_id: int) -> UpiAlert:
    alert = db.execute(
        select(UpiAlert).where(UpiAlert.id == alert_id).with_for_update()
    ).scalar_one_or_none()
    if alert is None or alert.shop_id != shop_id:
        raise ReconciliationError(f"alert {alert_id} not found")
    return alert


def load_baskets(db: Session, alert: UpiAlert, window_minutes: float) -> list[BasketView]:
    ref_time = func.coalesce(Basket.awaiting_since, Basket.last_activity_at, Basket.opened_at)
    window = timedelta(minutes=window_minutes)
    conds: list[ColumnElement[bool]] = [
        ref_time.between(alert.received_at - window, alert.received_at + window)
    ]
    if alert.ref_id:
        conds.append(Basket.upi_ref_id == alert.ref_id.upper())
    rows = db.execute(
        select(Basket).where(
            Basket.shop_id == alert.shop_id,
            Basket.status.in_(CANDIDATE_STATUSES),
            Basket.total_paise > 0,
            or_(*conds),
        )
    ).scalars()
    return [
        BasketView(
            id=b.id, total_paise=b.total_paise,
            ref_time=b.awaiting_since or b.last_activity_at or b.opened_at,
            status=b.status, upi_ref_id=b.upi_ref_id, customer_id=b.customer_id,
        )
        for b in rows
    ]


def load_customers(db: Session, shop_id: int) -> list[CustomerView]:
    aliases: dict[int, list[str]] = {}
    for c in db.execute(select(Customer).where(Customer.shop_id == shop_id)).scalars():
        aliases[c.id] = [c.display_name]
    for a in db.execute(
        select(CustomerAlias).where(CustomerAlias.shop_id == shop_id)
    ).scalars():
        aliases.setdefault(a.customer_id, []).append(a.alias)
    balances: dict[int, int] = dict(
        db.execute(
            text("SELECT customer_id, balance_paise FROM udhaar_balances WHERE shop_id = :s"),
            {"s": shop_id},
        ).all()
    )
    return [
        CustomerView(id=cid, aliases=tuple(al), udhaar_balance_paise=int(balances.get(cid, 0)))
        for cid, al in aliases.items()
    ]


def _reminder_refs(db: Session, alert: UpiAlert) -> dict[str, int]:
    if not alert.ref_id:
        return {}
    row = db.execute(
        select(Reminder.ref_id, Reminder.customer_id).where(
            Reminder.shop_id == alert.shop_id, Reminder.ref_id == alert.ref_id.upper()
        )
    ).first()
    return {row[0]: row[1]} if row else {}


def learn_payer_alias(db: Session, shop_id: int, customer_id: int | None,
                      payer_name: str | None) -> None:
    """Remember a confirmed UPI payer name as a customer alias (system improves with use)."""
    if customer_id is None or not payer_name:
        return
    existing = db.execute(
        select(CustomerAlias.alias).where(CustomerAlias.customer_id == customer_id)
    ).scalars()
    if any(fuzz.ratio(payer_name, a, processor=utils.default_process) >= 95 for a in existing):
        return
    db.add(CustomerAlias(shop_id=shop_id, customer_id=customer_id, alias=payer_name,
                         source="upi"))


def _apply_sale_match(
    db: Session, alert: UpiAlert, basket: Basket, *, decided_by: str,
    tier: int | None, confidence: float | None,
) -> None:
    close_basket(db, basket, "upi", alert_id=alert.id, amount_paise=alert.amount_paise,
                 actor=decided_by)
    db.add(Match(shop_id=alert.shop_id, alert_id=alert.id, basket_id=basket.id, kind="sale",
                 customer_id=basket.customer_id, tier=tier, confidence=confidence,
                 decided_by=decided_by))
    alert.status = "matched"


def _apply_udhaar_match(
    db: Session, alert: UpiAlert, customer_id: int, *, decided_by: str,
    tier: int | None, confidence: float | None,
) -> None:
    record_payment(db, alert.shop_id, customer_id, alert.amount_paise, alert_id=alert.id,
                   actor=decided_by)
    db.add(Match(shop_id=alert.shop_id, alert_id=alert.id, kind="udhaar_payment",
                 customer_id=customer_id, tier=tier, confidence=confidence,
                 decided_by=decided_by))
    alert.status = "matched"


def _audit(db: Session, alert: UpiAlert, action: str, payload: dict[str, Any]) -> None:
    db.add(AuditLog(shop_id=alert.shop_id, actor=payload.get("decided_by", "auto"),
                    action=action, payload={"alert_id": alert.id, **payload}))


def reconcile_alert(
    db: Session, shop_id: int, alert_id: int, settings: Settings | None = None
) -> Decision | None:
    """Decide one alert. Returns None if it was already matched/ignored. Commits."""
    s = settings or get_settings()
    alert = _lock_alert(db, shop_id, alert_id)
    if alert.status in ("matched", "ignored"):
        db.rollback()
        return None

    view = AlertView(id=alert.id, amount_paise=alert.amount_paise,
                     received_at=alert.received_at, payer_name=alert.payer_name,
                     ref_id=alert.ref_id)
    baskets = load_baskets(db, alert, s.match_window_minutes)
    customers = load_customers(db, shop_id)
    decision = decide(view, baskets, customers, window_minutes=s.match_window_minutes,
                      threshold=s.auto_match_threshold, reminder_refs=_reminder_refs(db, alert))

    applied = False
    if decision.outcome == "auto_match" and decision.best is not None:
        basket = lock_basket(db, decision.best.basket_id)
        if basket is not None and basket.status in CANDIDATE_STATUSES:
            _apply_sale_match(db, alert, basket, decided_by="auto",
                              tier=decision.best.tier, confidence=decision.best.confidence)
            applied = True
        else:  # lost a race for this basket: let the merchant decide
            decision.outcome = "pending_confirm"
            decision.reason = "best basket was closed concurrently"
    elif decision.outcome == "auto_udhaar_payment" and decision.suggested_customer_id:
        _apply_udhaar_match(db, alert, decision.suggested_customer_id, decided_by="auto",
                            tier=1, confidence=0.99)
        applied = True

    if not applied:
        alert.status = "pending_confirm"
    alert.candidates = decision.as_dict()
    _audit(db, alert, "reconciliation", {
        "decided_by": "auto",
        "inputs": {"amount_paise": alert.amount_paise, "payer_name": alert.payer_name,
                   "ref_id": alert.ref_id, "received_at": alert.received_at.isoformat(),
                   "n_baskets": len(baskets)},
        "decision": decision.as_dict(),
    })
    db.commit()

    bus.publish("match_decided" if applied else "alert_received",
                {"alert_id": alert.id, "status": alert.status, **decision.as_dict()})
    return decision


MerchantChoice = Literal["basket", "udhaar_payment", "not_a_sale"]


def merchant_decide(
    db: Session, shop_id: int, alert_id: int, choice: MerchantChoice, *,
    basket_id: uuid.UUID | None = None, customer_id: int | None = None,
) -> UpiAlert:
    """One-tap resolution from the confirm queue. Commits."""
    alert = _lock_alert(db, shop_id, alert_id)
    if alert.status in ("matched", "ignored"):
        db.rollback()
        raise ReconciliationError(f"alert {alert_id} is already {alert.status}")

    scored = {c["basket_id"]: c for c in (alert.candidates or {}).get("candidates", [])}
    try:
        if choice == "basket":
            if basket_id is None:
                raise ReconciliationError("basket_id is required")
            basket = lock_basket(db, basket_id)
            if basket is None or basket.shop_id != shop_id:
                raise ReconciliationError(f"basket {basket_id} not found")
            if basket.status not in CANDIDATE_STATUSES:
                raise ReconciliationError(f"basket {basket_id} is already {basket.status}")
            cand = scored.get(str(basket_id), {})
            _apply_sale_match(db, alert, basket, decided_by="merchant",
                              tier=cand.get("tier"), confidence=cand.get("confidence"))
            learn_payer_alias(db, shop_id, basket.customer_id, alert.payer_name)
        elif choice == "udhaar_payment":
            if customer_id is None:
                raise ReconciliationError("customer_id is required")
            _apply_udhaar_match(db, alert, customer_id, decided_by="merchant",
                                tier=None, confidence=None)
            learn_payer_alias(db, shop_id, customer_id, alert.payer_name)
        elif choice == "not_a_sale":
            db.add(Match(shop_id=shop_id, alert_id=alert.id, kind="not_a_sale",
                         decided_by="merchant"))
            alert.status = "ignored"
        else:
            raise ReconciliationError(f"unknown choice {choice!r}")
    except (BasketCloseError, ValueError) as exc:
        db.rollback()
        raise ReconciliationError(str(exc)) from exc

    _audit(db, alert, "merchant_decision", {
        "decided_by": "merchant", "choice": choice,
        "basket_id": str(basket_id) if basket_id else None, "customer_id": customer_id,
    })
    db.commit()
    bus.publish("match_decided", {"alert_id": alert.id, "status": alert.status,
                                  "choice": choice, "basket_id": basket_id,
                                  "customer_id": customer_id})
    return alert


def rereconcile_pending(db: Session, shop_id: int, basket: Basket,
                        settings: Settings | None = None) -> list[Decision]:
    """A basket just started awaiting payment: retry alerts that arrived before it did
    (the customer paid before the merchant said 'UPI')."""
    s = settings or get_settings()
    since = (basket.awaiting_since or basket.opened_at) - timedelta(minutes=s.match_window_minutes)
    ids = db.execute(
        select(UpiAlert.id).where(
            UpiAlert.shop_id == shop_id,
            UpiAlert.status.in_(("unmatched", "pending_confirm")),
            UpiAlert.received_at >= since,
        ).order_by(UpiAlert.received_at)
    ).scalars().all()
    db.commit()
    out = []
    for alert_id in ids:
        d = reconcile_alert(db, shop_id, alert_id, s)
        if d is not None:
            out.append(d)
    return out
