"""Udhaar reminders (§5.11) and the background scheduler.

The daily job drafts a reminder for every customer whose balance is above the threshold
and who hasn't paid for N days. Each reminder carries its own UPI ref, so when the
customer pays through the link the alert is a tier-1 udhaar repayment. Reminders are
drafts: the merchant sends them; the system never messages customers by itself.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import AuditLog, Customer, Reminder, Shop
from app.events import bus
from app.money import format_inr
from app.notify.whatsapp import WhatsAppError, wa_link
from app.upi.links import new_ref_id, upi_pay_link

log = logging.getLogger(__name__)


class ReminderError(ValueError):
    pass


def compose_reminder(customer: str, shop: str, amount_paise: int, vpa: str,
                     pay_link: str) -> str:
    return (
        f"Namaste {customer} ji, {shop} mein aapka udhaar {format_inr(amount_paise)} baaki hai. "
        f"UPI se bhej sakte hain: {vpa}\n{pay_link}\n"
        "Dhanyavaad! / Thank you!"
    )


def _create(db: Session, shop: Shop, customer: Customer, amount_paise: int,
            now: datetime) -> Reminder:
    for _ in range(5):
        ref = new_ref_id()
        if db.execute(select(Reminder.id).where(Reminder.ref_id == ref)).first() is None:
            break
    pay_link = upi_pay_link(shop.upi_vpa, shop.name, amount_paise, ref,
                            f"Udhaar {customer.display_name}")
    message = compose_reminder(customer.display_name, shop.name, amount_paise, shop.upi_vpa,
                               pay_link)
    link = None
    if customer.phone:
        try:
            link = wa_link(customer.phone, message)
        except WhatsAppError:
            link = None
    r = Reminder(shop_id=shop.id, customer_id=customer.id, amount_paise=amount_paise,
                 ref_id=ref, pay_link=pay_link, message_text=message, wa_link=link,
                 status="draft", due_at=now)
    db.add(r)
    db.flush()
    return r


def _balances(db: Session, shop_id: int) -> list[dict[str, object]]:
    # Days are counted from the last payment, or from the first udhaar sale if never paid.
    return [dict(r) for r in db.execute(text("""
        SELECT b.customer_id, b.balance_paise, b.last_payment_at,
               (SELECT MIN(e.created_at) FROM ledger_entries e
                 WHERE e.customer_id = b.customer_id AND e.entry_type = 'sale_udhaar')
                 AS first_udhaar_at
        FROM udhaar_balances b WHERE b.shop_id = :s AND b.balance_paise > 0
    """), {"s": shop_id}).mappings()]


def run_udhaar_reminders(db: Session, shop_id: int, settings: Settings | None = None,
                         now: datetime | None = None) -> list[Reminder]:
    s = settings or get_settings()
    now = now or datetime.now(UTC)
    quiet = timedelta(days=s.udhaar_reminder_after_days)
    shop = db.get(Shop, shop_id)
    if shop is None:
        return []
    created = []
    for row in _balances(db, shop_id):
        balance = int(row["balance_paise"])  # type: ignore[call-overload]
        if balance <= s.udhaar_reminder_threshold_paise:
            continue
        since = row["last_payment_at"] or row["first_udhaar_at"]
        if since is None or now - since < quiet:  # type: ignore[operator]
            continue
        cid = int(row["customer_id"])  # type: ignore[call-overload]
        recent = db.execute(select(Reminder.id).where(
            Reminder.customer_id == cid,
            (Reminder.status == "draft") | (Reminder.created_at >= now - quiet),
        )).first()
        if recent is not None:
            continue  # one open/recent reminder per customer is enough
        customer = db.get(Customer, cid)
        assert customer is not None
        created.append(_create(db, shop, customer, balance, now))
    if created:
        db.add(AuditLog(shop_id=shop_id, actor="scheduler", action="udhaar_reminders",
                        payload={"reminder_ids": [r.id for r in created]}))
    db.commit()
    for r in created:
        bus.publish("reminder", {"reminder_id": r.id, "customer_id": r.customer_id,
                                 "amount_paise": r.amount_paise})
    return created


def remind_now(db: Session, shop_id: int, customer_id: int) -> Reminder:
    """Merchant tapped "send reminder": draft one regardless of the N-day rule."""
    shop = db.get(Shop, shop_id)
    customer = db.get(Customer, customer_id)
    if shop is None or customer is None or customer.shop_id != shop_id:
        raise ReminderError(f"customer {customer_id} not found")
    balance = db.execute(text(
        "SELECT balance_paise FROM udhaar_balances WHERE customer_id = :c"),
        {"c": customer_id}).scalar_one()
    if balance <= 0:
        raise ReminderError("customer owes nothing")
    for old in db.execute(select(Reminder).where(Reminder.customer_id == customer_id,
                                                 Reminder.status == "draft")).scalars():
        old.status = "dismissed"  # superseded by the fresh amount
    try:
        r = _create(db, shop, customer, int(balance), datetime.now(UTC))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ReminderError("could not allocate a reference; try again") from exc
    bus.publish("reminder", {"reminder_id": r.id, "customer_id": customer_id,
                             "amount_paise": r.amount_paise})
    return r


def set_reminder_status(db: Session, shop_id: int, reminder_id: int, status: str) -> Reminder:
    r = db.get(Reminder, reminder_id)
    if r is None or r.shop_id != shop_id:
        raise ReminderError(f"reminder {reminder_id} not found")
    if r.status != "draft":
        raise ReminderError(f"reminder is already {r.status}")
    r.status = status
    db.commit()
    return r


# ---- scheduler ---------------------------------------------------------------------------
def build_scheduler(settings: Settings | None = None) -> BackgroundScheduler:
    from app.baskets.engine import BasketEngine
    from app.db.session import get_session_factory
    from app.extraction.extractor import RuleExtractor
    from app.redis_client import get_redis

    s = settings or get_settings()

    def reminders_job() -> None:
        with get_session_factory()() as db:
            made = run_udhaar_reminders(db, s.demo_shop_id, s)
            log.info("udhaar reminder job drafted %d reminders", len(made))

    def idle_sweep_job() -> None:
        with get_session_factory()() as db:
            BasketEngine(db, get_redis(), s.demo_shop_id, RuleExtractor(), s).sweep_idle()

    # Docker Desktop's VM clock can jump a few seconds; don't warn about "missed" runs then.
    sched = BackgroundScheduler(timezone="Asia/Kolkata",
                                job_defaults={"misfire_grace_time": 60})
    sched.add_job(reminders_job, CronTrigger(hour=s.reminder_hour_ist, minute=0),
                  id="udhaar_reminders", coalesce=True, max_instances=1)
    sched.add_job(idle_sweep_job, "interval", seconds=60, id="basket_idle_sweep",
                  coalesce=True, max_instances=1)
    return sched
