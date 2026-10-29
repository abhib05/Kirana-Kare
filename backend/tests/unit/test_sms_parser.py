import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.alerts.ingest import alert_from_sms
from app.alerts.sms_parser import SmsParseError, idempotency_key, parse_sms

FIXTURES = [
    json.loads(line)
    for line in (Path(__file__).parents[1] / "fixtures" / "sms_samples.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]


@pytest.mark.parametrize("case", FIXTURES, ids=[c["name"] for c in FIXTURES])
def test_fixture(case):
    parsed = parse_sms(case["text"])
    assert parsed.amount_paise == case["amount_paise"]
    assert parsed.payer_name == case["payer_name"]
    assert parsed.ref_id == case["ref_id"]
    expected_date = date.fromisoformat(case["date"]) if case["date"] else None
    assert parsed.sms_date == expected_date


def test_time_of_day_is_kept_but_date_only_is_not():
    with_time = parse_sms(next(c["text"] for c in FIXTURES if c["name"] == "axis_info"))
    assert with_time.received_at is not None
    assert (with_time.received_at.hour, with_time.received_at.minute) == (10, 31)
    date_only = parse_sms(next(c["text"] for c in FIXTURES if c["name"] == "hdfc_vpa"))
    assert date_only.received_at is None


@pytest.mark.parametrize(
    "text",
    [
        "Rs.500.00 debited from a/c XX1234 on 02-11-26 to VPA shop@okaxis UPI Ref 430612345699",
        "You paid to Reddy Provisions Rs 300 via UPI",
        "Your OTP for login is 123456",
        "",
        "Hello, money credited soon",
    ],
)
def test_rejects_non_credit_messages(text):
    with pytest.raises(SmsParseError):
        parse_sms(text)


def test_idempotency_key_uses_utr_when_present():
    p = parse_sms(FIXTURES[0]["text"])
    t1 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 2, 11, 30, tzinfo=UTC)
    assert idempotency_key(p, t1) == idempotency_key(p, t2)


def test_idempotency_key_without_utr_uses_minute_bucket():
    p = parse_sms("PhonePe: ₹150 received from Ramesh Kumar")
    t = datetime(2026, 11, 2, 10, 0, 5, tzinfo=UTC)
    same_minute = datetime(2026, 11, 2, 10, 0, 55, tzinfo=UTC)
    next_minute = datetime(2026, 11, 2, 10, 1, 5, tzinfo=UTC)
    assert idempotency_key(p, t) == idempotency_key(p, same_minute)
    assert idempotency_key(p, t) != idempotency_key(p, next_minute)


def test_alert_from_sms_falls_back_to_now():
    now = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)
    alert = alert_from_sms(parse_sms("Lakshmi Devi paid you ₹85.00"), now=now)
    assert alert.received_at == now
    assert alert.source == "sms_paste"
    assert alert.amount_paise == 8500
