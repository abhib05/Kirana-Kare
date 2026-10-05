"""Transaction filter + item extraction from counter speech.

Rule-based by default (Hindi / Kannada / Telugu / English, romanised or mixed) so the app
runs offline. If ANTHROPIC_API_KEY is set, an LLM does the extraction and the rule-based
parser is the fallback.
"""
import json
import re
from dataclasses import dataclass, field

import httpx
from rapidfuzz import fuzz, process

from .config import get_settings

NUMBERS = {
    "ek": 1, "one": 1, "ondu": 1, "onde": 1, "oka": 1, "okati": 1,
    "do": 2, "two": 2, "eradu": 2, "rendu": 2,
    "teen": 3, "three": 3, "mooru": 3, "moodu": 3,
    "char": 4, "chaar": 4, "four": 4, "naalku": 4, "nalugu": 4,
    "paanch": 5, "panch": 5, "five": 5, "aidu": 5, "aidhu": 5,
    "chhe": 6, "six": 6, "aaru": 6,
    "saat": 7, "seven": 7, "elu": 7,
    "aath": 8, "eight": 8, "entu": 8,
    "nau": 9, "nine": 9, "ombattu": 9,
    "das": 10, "dus": 10, "ten": 10, "hattu": 10,
    "aadha": 0.5, "adha": 0.5, "half": 0.5, "ardha": 0.5,
    "dedh": 1.5, "derh": 1.5, "dhai": 2.5, "dhaai": 2.5,
}
AMBIGUOUS_NUMBERS = {"do"}  # "do" = two, but also "dena do" = "give"
UNITS = {
    "kilo": "kg", "kg": "kg", "kilogram": "kg", "gram": "g", "gm": "g", "g": "g",
    "packet": "pkt", "pkt": "pkt", "pack": "pkt", "paket": "pkt", "packets": "pkt",
    "litre": "l", "liter": "l", "ltr": "l", "l": "l",
    "dozen": "dozen", "darjan": "dozen", "piece": "pc", "pcs": "pc", "pc": "pc", "bottle": "pc",
}
CURRENCY = {"rs", "rupees", "rupee", "rupaye", "rupay", "rupayi"}
UDHAAR_WORDS = [
    "khate", "khata", "udhaar", "udhar", "uddhar", "baki", "baaki", "bakaya", "credit",
    "saala", "sala", "appu", "likh do", "likhdo",
]
CASH_WORDS = ["cash", "nakad", "nagad", "naqad"]
FILLER = set(
    "dena de do dijiye chahiye chahie aur and mujhe bhaiya bhai anna aunty uncle ji please "
    "hai ka ki ke ko mein me se kodi kodu ivvu ivvandi beku cheyyi likh".split()
)
STOP_NAME = set("ek do khate mein me ke ka ki likh de dena baki udhaar udhar khata aur cash upi".split())


@dataclass
class Line:
    item_id: int
    name: str
    qty: float
    unit: str
    price: float
    amount: float


@dataclass
class Extraction:
    is_transaction: bool
    lines: list = field(default_factory=list)
    udhaar: bool = False
    cash: bool = False
    customer: str | None = None
    stated_total: float | None = None
    lang: str = "hi"
    engine: str = "rules"

    @property
    def total(self):
        if self.lines:
            return round(sum(l.amount for l in self.lines), 2)
        return self.stated_total or 0.0


def detect_lang(text: str) -> str:
    if re.search(r"[ಀ-೿]", text) or re.search(r"\b(ondu|onde|eradu|sakkare|beku)\b", text, re.I):
        return "kn"
    if re.search(r"[ఀ-౿]", text) or re.search(r"\b(oka|rendu|panchadara|ivvu|ivvandi)\b", text, re.I):
        return "te"
    return "hi"


def _catalog_index(items):
    idx = {}
    for it in items:
        idx[it["name"].lower()] = it
        for a in it["aliases"]:
            idx[a.lower()] = it
    return idx


def _find_item(tokens, i, idx, names):
    """Longest exact span first (3,2,1 words), then fuzzy on a single word."""
    for n in (3, 2, 1):
        if i + n <= len(tokens) and " ".join(tokens[i:i + n]) in idx:
            return idx[" ".join(tokens[i:i + n])], n
    w = tokens[i]
    if len(w) >= 4 and w not in FILLER and w not in UNITS and w not in NUMBERS:
        m = process.extractOne(w, names, scorer=fuzz.ratio, score_cutoff=86)
        if m:
            return idx[m[0]], 1
    return None, 0


def _next_is_item_or_unit(tokens, i, idx):
    if i + 1 >= len(tokens):
        return False
    nxt = tokens[i + 1]
    return nxt in UNITS or nxt in idx or (i + 2 < len(tokens) and f"{nxt} {tokens[i + 2]}" in idx)


def _price_for(item, qty, unit):
    """Catalog price is per catalog unit; convert if the speaker used grams for a kg item."""
    if unit == "g" and item["unit"] == "kg":
        qty = qty / 1000
    return round(item["price"] * qty, 2)


def _find_customer(text, lines):
    taken = {w for l in lines for w in l.name.lower().split()} | STOP_NAME | FILLER
    m = re.search(r"([A-Za-z]{3,})\s+(?:ke|ka|ki)\s+(?:khate|khata|udhaar|udhar|baki)", text, re.I)
    if m and m.group(1).lower() not in taken:
        return m.group(1).title()
    m = re.search(
        r"(?:khate\s+mein|khate\s+me|udhaar|udhar|baki|khata)\s+(?:(?:likh\s+do|likhdo|likh\s+de|mein)\s+)?([A-Za-z]{3,})",
        text, re.I)
    if m and m.group(1).lower() not in taken:
        return m.group(1).title()
    return None


def extract_rules(text: str, items: list[dict]) -> Extraction:
    low = text.lower().replace("₹", " rs ")
    lang = detect_lang(text)
    idx = _catalog_index(items)
    names = list(idx.keys())
    tokens = re.findall(r"\d+(?:\.\d+)?|[^\W\d_]+", low)

    udhaar = any(w in low for w in UDHAAR_WORDS)
    cash = any(re.search(rf"\b{w}\b", low) for w in CASH_WORDS)

    stated_total = None
    m = re.search(r"(?:rs\.?|rupees?|rupaye)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:rs|rupees?|rupaye|rupay|rupayi)\b", low)
    if m:
        stated_total = float(m.group(1) or m.group(2))

    lines, qty, unit, i = [], None, None, 0
    while i < len(tokens):
        t = tokens[i]
        if re.fullmatch(r"\d+(?:\.\d+)?", t):
            if (i + 1 < len(tokens) and tokens[i + 1] in CURRENCY) or (i > 0 and tokens[i - 1] in CURRENCY):
                i += 1  # a price, not a quantity
                continue
            qty, i = float(t), i + 1
            continue
        if t in NUMBERS and (t not in AMBIGUOUS_NUMBERS or _next_is_item_or_unit(tokens, i, idx)):
            qty, i = NUMBERS[t], i + 1
            if qty == 1 and i < len(tokens) and tokens[i] in ("aadha", "adha", "half"):
                qty, i = 1.5, i + 1
            continue
        if t in UNITS:
            unit, i = UNITS[t], i + 1
            continue
        item, n = _find_item(tokens, i, idx, names)
        if item:
            q = qty if qty is not None else 1.0
            lines.append(Line(item["id"], item["name"], q, unit or item["unit"], item["price"],
                              _price_for(item, q, unit)))
            qty, unit, i = None, None, i + n
            continue
        i += 1

    customer = _find_customer(text, lines)
    is_tx = bool(lines) or (stated_total is not None and (udhaar or cash))
    return Extraction(is_tx, lines, udhaar, cash, customer, stated_total, lang)


LLM_PROMPT = """You are the transaction filter for an Indian kirana (grocery) store's counter microphone.
Given a transcript (Hindi/Kannada/Telugu/English, possibly romanised or code-mixed), decide whether it
describes a sale. Chit-chat is NOT a transaction. Match items to this catalog (use exact catalog names):
{catalog}
Reply with ONLY JSON: {{"is_transaction":bool,"lines":[{{"item":str,"qty":number}}],
"udhaar":bool,"cash":bool,"customer":str|null,"stated_total":number|null,"lang":"hi|kn|te|en"}}
qty is in the catalog unit. udhaar=true when the sale goes on credit (khate mein likh do, udhaar...)."""


def extract_llm(text: str, items: list[dict]) -> Extraction | None:
    s = get_settings()
    if not s.anthropic_key:
        return None
    catalog = "; ".join(f"{i['name']} ({i['unit']}, aliases: {', '.join(i['aliases'][:4])})" for i in items)
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": s.anthropic_key, "anthropic-version": "2023-06-01"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 400,
                  "system": LLM_PROMPT.format(catalog=catalog),
                  "messages": [{"role": "user", "content": text}]},
            timeout=8,
        )
        r.raise_for_status()
        raw = r.json()["content"][0]["text"]
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except Exception:
        return None
    by_name = {i["name"].lower(): i for i in items}
    lines = []
    for l in data.get("lines", []):
        it = by_name.get(str(l.get("item", "")).lower())
        if it:
            q = float(l.get("qty") or 1)
            lines.append(Line(it["id"], it["name"], q, it["unit"], it["price"], round(it["price"] * q, 2)))
    stated = data.get("stated_total")
    return Extraction(bool(data.get("is_transaction")) and (bool(lines) or bool(stated)), lines,
                      bool(data.get("udhaar")), bool(data.get("cash")), data.get("customer"),
                      stated, data.get("lang") or detect_lang(text), "llm")


def extract(text: str, items: list[dict]) -> Extraction:
    return extract_llm(text, items) or extract_rules(text, items)
