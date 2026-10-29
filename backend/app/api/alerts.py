from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.alerts.ingest import AlertIn, alert_from_sms, ingest_alert
from app.alerts.sms_parser import SmsParseError, parse_sms
from app.api.deps import get_db, shop_id
from app.api.schemas import AlertOut, IngestResult

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class SmsIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _ingest(db: Session, shop: int, alert: AlertIn) -> IngestResult:
    try:
        row, created = ingest_alert(db, shop, alert)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return IngestResult(alert=AlertOut.model_validate(row), duplicate=not created)


@router.post("", response_model=IngestResult)
def post_alert(
    alert: AlertIn, db: Session = Depends(get_db), shop: int = Depends(shop_id)
) -> IngestResult:
    return _ingest(db, shop, alert)


@router.post("/parse-sms", response_model=IngestResult)
def parse_sms_alert(
    body: SmsIn, db: Session = Depends(get_db), shop: int = Depends(shop_id)
) -> IngestResult:
    try:
        parsed = parse_sms(body.text)
    except SmsParseError as exc:
        raise HTTPException(422, f"could not parse SMS: {exc}") from exc
    return _ingest(db, shop, alert_from_sms(parsed))
