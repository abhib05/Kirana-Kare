"""Scores how well a UPI alert matches an open basket.

Confidence = amount (0.60, must be exact) + time proximity (up to 0.25, decaying over the match
window) + payer-name similarity (up to 0.15). Competing baskets with the same amount cost 0.20.
Exact tag-ref matches (ref ID inside the pay link) bypass scoring entirely.
"""
from rapidfuzz import fuzz


def name_score(payer: str | None, names: list[str]) -> float:
    if not payer or not names:
        return 0.0
    best = max(fuzz.token_set_ratio(payer.lower(), n.lower()) for n in names)
    return best / 100.0


def score(alert: dict, basket: dict, customer_names: list[str], window_s: int, rivals: int) -> tuple[float, str]:
    if abs(alert["amount"] - basket["total"]) > 0.01:
        return 0.0, "amount differs"
    dt = abs(alert["ts"] - basket["ts"])
    if dt > window_s:
        return 0.0, "outside time window"
    s = 0.60 + 0.25 * max(0.0, 1 - dt / window_s)
    reasons = [f"amount ₹{alert['amount']:g} exact", f"{int(dt)}s apart"]
    ns = name_score(alert.get("payer"), customer_names)
    if ns >= 0.6:
        s += 0.15 * ns
        reasons.append(f"payer '{alert['payer']}' ~ known name")
    if rivals > 1:
        s -= 0.20
        reasons.append(f"{rivals} baskets share this amount")
    return round(min(max(s, 0.0), 1.0), 3), "; ".join(reasons)
