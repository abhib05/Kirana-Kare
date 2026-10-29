"""Response models shared by the REST routers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    idempotency_key: str
    amount_paise: int
    payer_name: str | None
    ref_id: str | None
    received_at: datetime
    source: str
    status: str
    candidates: dict[str, Any] | None = None


class IngestResult(BaseModel):
    alert: AlertOut
    duplicate: bool
    decision: dict[str, Any] | None = None


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entry_type: str
    amount_paise: int
    customer_id: int | None
    basket_id: uuid.UUID | None
    alert_id: int | None
    reverses_entry_id: int | None
    memo: str | None
    created_at: datetime
