"""UPI deep links (upi://pay) and the short reference IDs we embed in them."""

from __future__ import annotations

import secrets
from urllib.parse import quote, urlencode

from app.money import upi_amount

_REF_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # RFC 4648 base32: URL-safe, no 0/1/8/9
REF_PREFIX = "KK"
REF_LEN = 6


def new_ref_id() -> str:
    """'KK' + 6 random base32 chars (32^6 ≈ 1.07e9 values). Uniqueness is enforced by a
    UNIQUE column; callers retry on the (rare) collision."""
    return REF_PREFIX + "".join(secrets.choice(_REF_ALPHABET) for _ in range(REF_LEN))


def is_ref_id(value: str | None) -> bool:
    return (
        value is not None
        and len(value) == len(REF_PREFIX) + REF_LEN
        and value.startswith(REF_PREFIX)
        and all(c in _REF_ALPHABET for c in value[len(REF_PREFIX):])
    )


def upi_pay_link(vpa: str, payee_name: str, amount_paise: int, ref_id: str, note: str) -> str:
    params = {
        "pa": vpa,
        "pn": payee_name,
        "am": upi_amount(amount_paise),
        "cu": "INR",
        "tr": ref_id,
        "tn": note[:50],
    }
    return "upi://pay?" + urlencode(params, quote_via=quote)
