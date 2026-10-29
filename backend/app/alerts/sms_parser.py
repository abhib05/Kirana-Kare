"""Parse pasted bank SMS / UPI app notifications into a structured credit alert.

Regex-first. Each pattern targets one family of real-world formats (see
tests/fixtures/sms_samples.jsonl). Debit messages are rejected explicitly so a
"Rs 500 debited" SMS can never be booked as a sale.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

from app.money import rupees_to_paise

IST = timezone(timedelta(hours=5, minutes=30))


class SmsParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedSms:
    amount_paise: int
    payer_name: str | None
    ref_id: str | None  # our KK ref if the payer's note echoed it, else the bank UTR
    utr: str | None
    # Only set when the message carries a time of day. A date-only stamp (midnight) would
    # wreck the reconciliation time window, so the caller falls back to "now" instead.
    received_at: datetime | None
    sms_date: date | None
    raw_text: str


_AMOUNT = r"(?:rs\.?|inr|₹)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
_AMOUNT_RE = re.compile(_AMOUNT, re.I)
_CREDIT_WORDS = re.compile(r"\b(credited|received|paid you|deposited|credit)\b", re.I)
_DEBIT_WORDS = re.compile(r"\b(debited|withdrawn|spent|paid to|sent to|debit)\b", re.I)
_KK_REF = re.compile(r"\b(KK[A-Z2-7]{6})\b")
_UTR_PATTERNS = [
    re.compile(r"(?:UPI\s*Ref(?:erence)?\.?\s*(?:No\.?|number)?|Ref\.?\s*No\.?|UTR(?:\s*No\.?)?|"
               r"UPI\s*txn\s*id|Txn\s*ID|UPI)\s*[:\-]?\s*([0-9]{10,16})", re.I),
    re.compile(r"UPI/(?:P2[AM]|CR)/([0-9]{10,16})/", re.I),
]

# Payer name patterns, most specific first. Group 1 is the name (or VPA).
_PAYER_PATTERNS = [
    re.compile(r"UPI/(?:P2[AM]|CR)/[0-9]{10,16}/([A-Za-z][A-Za-z .]{1,40}?)/", re.I),
    re.compile(r"transfer from\s+([A-Za-z][A-Za-z .]{1,40}?)\s+(?:Ref|on|UPI)", re.I),
    re.compile(r";\s*([A-Za-z][A-Za-z .]{1,40}?)\s+credited", re.I),
    re.compile(r"^\s*([A-Za-z][A-Za-z .]{1,40}?)\s+(?:has\s+)?paid you", re.I),
    re.compile(r"(?:by|from)\s+(?:a/c linked to\s+)?VPA\s+([\w.\-]+@[\w]+)", re.I),
    re.compile(r"from\s+([\w.\-]+@[\w]+)", re.I),
    re.compile(r"from\s+([A-Za-z][A-Za-z .]{1,40}?)\s*(?:\(|\.|,|via|on\b|UPI|$)", re.I),
]

_DATE_PATTERNS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"(\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})"), ["%d-%m-%Y %H:%M:%S"]),
    (re.compile(r"(\d{2}-[A-Za-z]{3}-\d{2,4})"), ["%d-%b-%y", "%d-%b-%Y"]),
    (re.compile(r"(\d{2}[A-Za-z]{3}\d{2})"), ["%d%b%y"]),
    (re.compile(r"(\d{2}-\d{2}-\d{2,4})"), ["%d-%m-%y", "%d-%m-%Y"]),
    (re.compile(r"(\d{2}/\d{2}/\d{2,4})"), ["%d/%m/%y", "%d/%m/%Y"]),
]

_NOISE_NAMES = {"your", "a/c", "ac", "account", "upi", "bank", "vpa", "mobile"}


def _clean_name(raw: str) -> str | None:
    name = raw.strip(" .,")
    if "@" in name:  # VPA -> local part, e.g. ramesh.k@okicici -> "ramesh k"
        name = re.sub(r"[._\-0-9]+", " ", name.split("@", 1)[0]).strip()
    name = re.sub(r"\s+", " ", name)
    if not name or name.lower() in _NOISE_NAMES or len(name) < 2:
        return None
    return name


def _parse_date(text: str) -> tuple[datetime | None, bool]:
    """Returns (timestamp in IST, whether it included a time of day)."""
    for pattern, fmts in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        for fmt in fmts:
            try:
                return datetime.strptime(m.group(1), fmt).replace(tzinfo=IST), "%H" in fmt
            except ValueError:
                continue
    return None, False


def parse_sms(text: str) -> ParsedSms:
    raw = text.strip()
    if not raw:
        raise SmsParseError("empty text")
    amount_m = _AMOUNT_RE.search(raw)
    if not amount_m:
        raise SmsParseError("no amount found")
    credit, debit = _CREDIT_WORDS.search(raw), _DEBIT_WORDS.search(raw)
    if debit and (not credit or debit.start() < credit.start()):
        raise SmsParseError("this looks like a debit, not a payment received")
    if not credit:
        raise SmsParseError("no credit/received wording found")
    amount_paise = rupees_to_paise(amount_m.group(1))
    if amount_paise <= 0:
        raise SmsParseError("amount must be positive")

    utr = None
    for p in _UTR_PATTERNS:
        if m := p.search(raw):
            utr = m.group(1)
            break
    kk = _KK_REF.search(raw)

    payer = None
    for p in _PAYER_PATTERNS:
        if m := p.search(raw):
            payer = _clean_name(m.group(1))
            if payer:
                break

    stamp, has_time = _parse_date(raw)
    return ParsedSms(
        amount_paise=amount_paise,
        payer_name=payer,
        ref_id=kk.group(1) if kk else utr,
        utr=utr,
        received_at=stamp if has_time else None,
        sms_date=stamp.date() if stamp else None,
        raw_text=raw,
    )


def idempotency_key(parsed: ParsedSms, received_at: datetime) -> str:
    """hash(amount, UTR) when a UTR exists (globally unique per payment), else
    hash(amount, payer, minute bucket) so a re-paste of the same message is a no-op."""
    if parsed.utr:
        basis = f"{parsed.amount_paise}|utr:{parsed.utr}"
    else:
        minute = received_at.astimezone(UTC).strftime("%Y%m%d%H%M")
        basis = f"{parsed.amount_paise}|{(parsed.payer_name or '').lower()}|{minute}"
    return "sms:" + hashlib.sha256(basis.encode()).hexdigest()[:32]
