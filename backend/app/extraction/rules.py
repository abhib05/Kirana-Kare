"""Deterministic rule-based extractor for code-mixed counter speech.

Deliberately simple: catalog term spotting + Hindi/Telugu/English number words + cue
phrases. It is the CI-safe extractor and the eval baseline the LLM must beat; it is not
meant to understand open-ended conversation.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.extraction.catalog_match import CatalogEntry
from app.extraction.schemas import Action, ExtractedItem, ExtractedTurn, PaymentMode

NUMBER_WORDS: dict[str, Decimal] = {
    # Hindi
    "ek": Decimal(1), "do": Decimal(2), "teen": Decimal(3), "char": Decimal(4),
    "chaar": Decimal(4), "paanch": Decimal(5), "panch": Decimal(5), "chhe": Decimal(6),
    "saat": Decimal(7), "aath": Decimal(8), "nau": Decimal(9), "das": Decimal(10),
    "aadha": Decimal("0.5"), "adha": Decimal("0.5"), "aadhi": Decimal("0.5"),
    "dedh": Decimal("1.5"), "derh": Decimal("1.5"), "dhai": Decimal("2.5"),
    "dhaai": Decimal("2.5"), "sava": Decimal("1.25"), "pauna": Decimal("0.75"),
    # Telugu
    "okati": Decimal(1), "oka": Decimal(1), "rendu": Decimal(2), "moodu": Decimal(3),
    "mudu": Decimal(3), "naalugu": Decimal(4), "aidu": Decimal(5), "ara": Decimal("0.5"),
    # English
    "one": Decimal(1), "two": Decimal(2), "three": Decimal(3), "four": Decimal(4),
    "five": Decimal(5), "six": Decimal(6), "ten": Decimal(10), "half": Decimal("0.5"),
    "a": Decimal(1), "an": Decimal(1),
}
UNIT_WORDS = {
    "kilo": "kg", "kg": "kg", "kgs": "kg", "kilos": "kg",
    "gram": "g", "grams": "g", "g": "g", "gm": "g", "gms": "g",
    "litre": "l", "liter": "l", "litres": "l", "ltr": "l", "l": "l",
    "ml": "ml",
    "packet": "packet", "packets": "packet", "pack": "packet", "paket": "packet",
    "dozen": "dozen", "darjan": "dozen",
    "piece": "pcs", "pieces": "pcs", "pcs": "pcs", "nos": "pcs", "bottle": "pcs",
}
RUPEE_WORDS = {"rupaye", "rupay", "rupees", "rupee", "rs", "rupiya", "rupayalu", "₹"}

CASH_CUES = ("cash", "nakad", "nagad", "naqad", "chhutta")
UPI_CUES = ("upi", "gpay", "google pay", "phonepe", "phone pe", "paytm", "scan", "qr",
            "online")
UDHAAR_CUES = ("udhaar", "udhar", "khata", "khate", "credit", "likh do", "likh lo",
               "baad mein", "baad me")
CLOSE_CUES = ("bas", "total", "bill", "kitna hua", "kitna huwa", "ho gaya", "itna hi",
              "thats all", "that's all", "anthe", "enta")
REMOVE_CUES = ("nahi chahiye", "nahin chahiye", "mat do", "hata do", "hatao", "cancel",
               "remove", "wapas rakh", "vaddu")
REPAY_CUES = ("jama", "chukaya", "chuka diya", "wapas de", "repay", "udhaar ka paisa",
              "baaki ka")
HONORIFIC_RE = re.compile(
    r"\b([a-z]{3,})\s+(bhaiya|bhai|ji|anna|amma|didi|aunty|garu|ben|sir|madam|akka)\b")
KHATA_NAME_RE = re.compile(r"\b([a-z]{3,})\s+(?:ke|ka|ki)\s+(?:khate|khata|naam|account)")
NAME_STOP = {"haan", "theek", "accha", "achha", "aap", "kya", "nahi", "bas", "aur", "dena",
             "bhaiya", "thoda", "sab", "kal", "abhi", "please", "chalo", "hello", "namaste"}


def _normalize(text: str) -> str:
    t = text.lower().replace("₹", " ₹ ")
    t = re.sub(r"(\d)([a-z])", r"\1 \2", t)
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)
    t = re.sub(r"[^\w₹.' ]+", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _has(text: str, cues: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(c)}(?!\w)", text) for c in cues)


def _num(tok: str) -> Decimal | None:
    if re.fullmatch(r"\d+(?:\.\d+)?", tok):
        return Decimal(tok)
    return NUMBER_WORDS.get(tok)


def _is_price_at(tokens: list[str], i: int) -> bool:
    return (i + 1 < len(tokens) and tokens[i + 1] in RUPEE_WORDS) or (
        i > 0 and tokens[i - 1] == "₹")


def _price_leads(tokens: list[str], i: int) -> bool:
    """'90 rupaye ka do kilo chawal': a price followed by ka/ki/ke belongs to what follows."""
    return i + 2 < len(tokens) and tokens[i + 2] in ("ka", "ki", "ke")


def _find_price(tokens: list[str], lo: int, hi: int, leading: bool | None = None
                ) -> int | None:
    for i in range(lo, hi):
        if re.fullmatch(r"\d+(?:\.\d+)?", tokens[i]) and _is_price_at(tokens, i):
            if leading is not None and _price_leads(tokens, i) != leading:
                continue
            return int(Decimal(tokens[i]) * 100)
    return None


def _qty_unit(tokens: list[str], lo: int, hi: int, backward: bool
              ) -> tuple[Decimal | None, str | None]:
    rng = range(hi - 1, lo - 1, -1) if backward else range(lo, hi)
    for i in rng:
        n = _num(tokens[i])
        if n is None or _is_price_at(tokens, i):
            continue
        if tokens[i] in ("a", "an") and not (i + 1 < hi and tokens[i + 1] in UNIT_WORDS):
            continue  # bare English article is not a quantity
        unit = UNIT_WORDS.get(tokens[i + 1]) if i + 1 < len(tokens) else None
        return n, unit
    # unit with no number ("kilo atta") means one of that unit
    for i in rng:
        if tokens[i] in UNIT_WORDS:
            return Decimal(1), UNIT_WORDS[tokens[i]]
    return None, None


def _term_index(catalog: list[CatalogEntry]) -> list[tuple[tuple[str, ...], CatalogEntry]]:
    idx = []
    for p in catalog:
        for term in p.terms:
            toks = tuple(_normalize(term).split())
            if toks and not (len(toks) == 1 and (toks[0] in UNIT_WORDS or toks[0] in
                                                 NUMBER_WORDS)):
                idx.append((toks, p))
    idx.sort(key=lambda x: -len(x[0]))  # longest phrase wins ("basmati rice" over "rice")
    return idx


def _mentions(tokens: list[str], catalog: list[CatalogEntry]
              ) -> list[tuple[int, int, CatalogEntry]]:
    index = _term_index(catalog)
    out: list[tuple[int, int, CatalogEntry]] = []
    i = 0
    while i < len(tokens):
        for toks, product in index:
            n = len(toks)
            if tuple(tokens[i:i + n]) == toks:
                out.append((i, i + n, product))
                i += n
                break
        else:
            i += 1
    return out


def _customer_name(text: str, catalog_words: set[str]) -> str | None:
    for rx in (KHATA_NAME_RE, HONORIFIC_RE):
        for m in rx.finditer(text):
            name = m.group(1)
            if name in NAME_STOP or name in catalog_words or name in NUMBER_WORDS:
                continue
            return m.group(0) if rx is HONORIFIC_RE else name
    return None


def extract_rules(text: str, catalog: list[CatalogEntry]) -> ExtractedTurn:
    norm = _normalize(text)
    tokens = norm.split()
    catalog_words = {w for p in catalog for t in p.terms for w in _normalize(t).split()}
    customer = _customer_name(norm, catalog_words)

    mode: PaymentMode = "unknown"
    if _has(norm, UPI_CUES):
        mode = "upi"
    elif _has(norm, UDHAAR_CUES) and not _has(norm, REPAY_CUES):
        mode = "udhaar"
    elif _has(norm, CASH_CUES):
        mode = "cash"

    if _has(norm, REPAY_CUES):
        amount = _find_price(tokens, 0, len(tokens))
        if amount is None:  # "500 jama" without the word rupaye
            nums = [t for t in tokens if re.fullmatch(r"\d+", t)]
            amount = int(nums[0]) * 100 if nums else None
        return ExtractedTurn(
            is_transactional=amount is not None, payment_mode="upi" if mode == "upi" else "cash",
            customer_name=customer, action="udhaar_payment" if amount else "none",
            udhaar_payment_paise=amount,
        )

    items: list[ExtractedItem] = []
    mentions = _mentions(tokens, catalog)
    for k, (start, end, product) in enumerate(mentions):
        prev_end = mentions[k - 1][1] if k else 0
        next_start = mentions[k + 1][0] if k + 1 < len(mentions) else len(tokens)
        qty, unit = _qty_unit(tokens, max(prev_end, start - 3), start, backward=True)
        if qty is None:
            qty, unit = _qty_unit(tokens, end, min(next_start, end + 3), backward=False)
        qty = qty or Decimal(1)
        line_price = _find_price(tokens, prev_end, start, leading=True)
        if line_price is None:
            line_price = _find_price(tokens, end, next_start, leading=False)
        unit_price = int((Decimal(line_price) / qty).to_integral_value()) if line_price else None
        lo = max(prev_end, start - 3)
        items.append(ExtractedItem(
            raw_text=" ".join(tokens[lo:end]),
            product_hint=product.name.lower(),
            qty=qty, unit=unit, spoken_price_paise=unit_price,  # type: ignore[arg-type]
        ))

    removing = _has(norm, REMOVE_CUES)
    closing = _has(norm, CLOSE_CUES) or mode != "unknown"
    action: Action
    if removing and items:
        action = "remove_items"
    elif closing:
        action = "close_basket"
    elif items:
        action = "add_items"
    else:
        action = "none"
    transactional = bool(items) or closing or (customer is not None and mode == "udhaar")
    return ExtractedTurn(
        is_transactional=transactional, items=items, payment_mode=mode,
        customer_name=customer, action=action,
    )
