"""Parse UPI credit notifications (PhonePe / GPay / Paytm / bank SMS) into structured alerts."""
import re

AMOUNT = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.I)
AMOUNT_AFTER = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:₹|rs\b|inr\b)", re.I)
PAYER = [
    re.compile(r"(?:received|credited).{0,80}?\bfrom\s+([A-Za-z][A-Za-z .'-]{1,40}?)(?=\s+(?:via|on|using|ref|upi|vpa|to|at|\()|[.,;]|$)", re.I),
    re.compile(r"\b([A-Za-z][A-Za-z .'-]{1,40}?)\s+(?:paid|sent)\s+you", re.I),
    re.compile(r"\bfrom\s+(?:VPA\s+)?[\w.\-]+@[\w]+\s*\(([A-Za-z .'-]+)\)", re.I),
]
UPI_REF = re.compile(r"(?:upi\s*ref(?:erence)?(?:\s*no)?|ref(?:erence)?\s*(?:no|id)?|txn\s*id|utr)[:.\s#-]*([A-Z0-9]{6,20})", re.I)
TAG_REF = re.compile(r"\b(KC-[A-Z]\d+)\b", re.I)
APPS = {"phonepe": "PhonePe", "gpay": "GPay", "google pay": "GPay", "paytm": "Paytm", "bhim": "BHIM"}


def parse_alert(raw: str) -> dict | None:
    """Returns {amount, payer, upi_ref, tag_ref, app} or None if this isn't a UPI credit."""
    if not re.search(r"received|credited|paid you|sent you", raw, re.I):
        return None
    if re.search(r"\b(debited|you paid|sent to)\b", raw, re.I) and not re.search(r"received|credited", raw, re.I):
        return None
    m = AMOUNT.search(raw) or AMOUNT_AFTER.search(raw)
    if not m:
        return None
    amount = float(m.group(1).replace(",", ""))
    payer = None
    for p in PAYER:
        pm = p.search(raw)
        if pm:
            payer = re.sub(r"\s+", " ", pm.group(1)).strip(" .")
            break
    ref = UPI_REF.search(raw)
    tag = TAG_REF.search(raw)
    app = next((v for k, v in APPS.items() if k in raw.lower()), "UPI")
    return {
        "amount": amount,
        "payer": payer,
        "upi_ref": ref.group(1) if ref else None,
        "tag_ref": tag.group(1).upper() if tag else None,
        "app": app,
    }
