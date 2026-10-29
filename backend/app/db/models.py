"""SQLAlchemy models for §4 of the plan.

All money columns are BIGINT paise. Every table carries shop_id so the schema is
multi-tenant ready even though the MVP runs a single demo shop.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Shop(Base):
    __tablename__ = "shops"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    upi_vpa: Mapped[str] = mapped_column(String(100))
    language_bias: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    created_at: Mapped[datetime] = _created_at()


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = _created_at()

    aliases: Mapped[list[CustomerAlias]] = relationship(back_populates="customer")


class CustomerAlias(Base):
    __tablename__ = "customer_aliases"
    __table_args__ = (
        CheckConstraint("source IN ('voice','upi','manual')", name="ck_alias_source"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    alias: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = _created_at()

    customer: Mapped[Customer] = relationship(back_populates="aliases")


class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = _created_at()


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    unit: Mapped[str] = mapped_column(String(10))
    price_paise: Mapped[int] = mapped_column(BigInteger)
    stock_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    reorder_level: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"))
    created_at: Mapped[datetime] = _created_at()


class Basket(Base):
    __tablename__ = "baskets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','awaiting_payment','closed','cancelled')", name="ck_basket_status"
        ),
        CheckConstraint(
            "payment_mode IS NULL OR payment_mode IN ('cash','upi','udhaar','mixed')",
            name="ck_basket_payment_mode",
        ),
        Index("ix_baskets_shop_status", "shop_id", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    customer_name_hint: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    total_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    payment_mode: Mapped[str | None] = mapped_column(String(10))
    upi_ref_id: Mapped[str | None] = mapped_column(String(20), unique=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    awaiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upi_link: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_turn_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    created_at: Mapped[datetime] = _created_at()

    items: Mapped[list[BasketItem]] = relationship(
        back_populates="basket", order_by="BasketItem.id", cascade="all, delete-orphan"
    )


class BasketItem(Base):
    __tablename__ = "basket_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    basket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("baskets.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    raw_text: Mapped[str] = mapped_column(Text)
    product_hint: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit: Mapped[str | None] = mapped_column(String(10))
    unit_price_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    line_total_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    match_score: Mapped[float | None] = mapped_column(Float)
    flagged: Mapped[bool] = mapped_column(default=False)
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = _created_at()

    basket: Mapped[Basket] = relationship(back_populates="items")


class UpiAlert(Base):
    __tablename__ = "upi_alerts"
    __table_args__ = (
        CheckConstraint(
            "source IN ('simulated','sms_paste','webhook')", name="ck_alert_source"
        ),
        CheckConstraint(
            "status IN ('unmatched','matched','pending_confirm','ignored')",
            name="ck_alert_status",
        ),
        CheckConstraint("amount_paise > 0", name="ck_alert_amount_positive"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    payer_name: Mapped[str | None] = mapped_column(Text)
    ref_id: Mapped[str | None] = mapped_column(String(40))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(20), default="unmatched")
    # The last reconciliation decision: outcome, scored top candidates, udhaar suggestion.
    candidates: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint("decided_by IN ('auto','merchant')", name="ck_match_decided_by"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    alert_id: Mapped[int] = mapped_column(ForeignKey("upi_alerts.id"), unique=True)
    basket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("baskets.id"))
    # 'sale' (basket), 'udhaar_payment', or 'not_a_sale'
    kind: Mapped[str] = mapped_column(String(20), default="sale")
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    tier: Mapped[int | None] = mapped_column(SmallInteger)
    confidence: Mapped[float | None] = mapped_column(Float)
    decided_by: Mapped[str] = mapped_column(String(10))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = _created_at()


class LedgerEntry(Base):
    """APPEND-ONLY. A DB trigger rejects UPDATE/DELETE; write only via ledger.service.post."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "entry_type IN ('sale_cash','sale_upi','sale_udhaar','udhaar_payment','reversal')",
            name="ck_ledger_entry_type",
        ),
        CheckConstraint("amount_paise > 0", name="ck_ledger_amount_positive"),
        CheckConstraint(
            "(entry_type = 'reversal') = (reverses_entry_id IS NOT NULL)",
            name="ck_ledger_reversal_ref",
        ),
        Index(
            "uq_ledger_reverses_once",
            "reverses_entry_id",
            unique=True,
            postgresql_where="reverses_entry_id IS NOT NULL",
        ),
        Index("ix_ledger_shop_customer", "shop_id", "customer_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    entry_type: Mapped[str] = mapped_column(String(20))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    basket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("baskets.id"))
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("upi_alerts.id"))
    reverses_entry_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_entries.id"))
    memo: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class RestockDraft(Base):
    __tablename__ = "restock_drafts"
    __table_args__ = (
        CheckConstraint("status IN ('draft','sent','dismissed')", name="ck_restock_status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"))
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    message_text: Mapped[str] = mapped_column(Text)
    wa_link: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="draft")
    created_at: Mapped[datetime] = _created_at()


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        CheckConstraint("status IN ('draft','sent','dismissed')", name="ck_reminder_status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    ref_id: Mapped[str] = mapped_column(String(20), unique=True)
    pay_link: Mapped[str] = mapped_column(Text)
    message_text: Mapped[str] = mapped_column(Text)
    wa_link: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="draft")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    actor: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created_at()
