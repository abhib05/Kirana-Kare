"""UPI alert ingestion. Every alert carries an idempotency key; ingesting the same key twice
returns the original row and changes nothing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.alerts.sms_parser import ParsedSms, idempotency_key
from app.db.models import AuditLog, UpiAlert


class AlertIn(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    amount_paise: int = Field(gt=0, lt=10_000_000_00)  # < ₹1 crore
    payer_name: str | None = Field(default=None, max_length=200)
    ref_id: str | None = Field(default=None, max_length=40)
    received_at: AwareDatetime
    source: Literal["simulated", "sms_paste", "webhook"] = "simulated"
    raw_text: str | None = None

    @field_validator("payer_name", "ref_id")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


def ingest_alert(db: Session, shop_id: int, alert: AlertIn) -> tuple[UpiAlert, bool]:
    """Insert the alert unless its idempotency key exists. Returns (row, created).

    `ON CONFLICT DO NOTHING` makes this safe under concurrent duplicate deliveries: exactly
    one insert wins, every other caller reads the winner's row.
    """
    stmt = (
        insert(UpiAlert)
        .values(
            shop_id=shop_id,
            idempotency_key=alert.idempotency_key,
            amount_paise=alert.amount_paise,
            payer_name=alert.payer_name,
            ref_id=alert.ref_id.upper() if alert.ref_id else None,
            received_at=alert.received_at,
            raw_text=alert.raw_text,
            source=alert.source,
            status="unmatched",
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(UpiAlert.id)
    )
    new_id = db.execute(stmt).scalar_one_or_none()
    created = new_id is not None
    if created:
        db.add(
            AuditLog(
                shop_id=shop_id,
                actor="system",
                action="alert_ingested",
                payload={"alert_id": new_id, **alert.model_dump(mode="json")},
            )
        )
    db.commit()
    row = db.execute(
        select(UpiAlert).where(UpiAlert.idempotency_key == alert.idempotency_key)
    ).scalar_one()
    if row.shop_id != shop_id:  # key collision across shops must never leak another shop's alert
        raise ValueError("idempotency key already used by another shop")
    return row, created


def alert_from_sms(parsed: ParsedSms, now: datetime | None = None) -> AlertIn:
    received_at = parsed.received_at or now or datetime.now(UTC)
    return AlertIn(
        idempotency_key=idempotency_key(parsed, received_at),
        amount_paise=parsed.amount_paise,
        payer_name=parsed.payer_name,
        ref_id=parsed.ref_id,
        received_at=received_at,
        source="sms_paste",
        raw_text=parsed.raw_text,
    )
