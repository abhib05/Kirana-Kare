"""Pure scoring + decision logic for matching a UPI alert to a basket (§5.6).

No database here: the engine loads plain dataclasses and calls `decide()`, which makes
every rule table-testable and lets the eval harness reuse the exact production logic.

Tiers
  1  alert ref_id == basket's generated UPI ref           -> 0.99
  2  exact amount and basket time within ±window          -> 0.75, then adjusted:
       +0.10 if it is the only same-amount candidate
       -0.25 per additional same-amount candidate
       -0.05 per minute away beyond 2 minutes
  3  payer name fuzzy-matches the basket customer (>= 80) -> +0.15 evidence
     payer name matches a *different* known customer      -> -0.10
     (name-only candidates with a different amount start at 0.35 and never auto-match)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from rapidfuzz import fuzz, utils

TIER1_CONFIDENCE = 0.99
TIER2_BASE = 0.75
TIER2_UNIQUE_BONUS = 0.10
TIER2_PER_EXTRA_CANDIDATE = 0.25
TIER2_PER_MINUTE = 0.05
TIER2_GRACE_MINUTES = 2.0
TIER3_BASE = 0.35
NAME_MATCH_BONUS = 0.15
NAME_OTHER_CUSTOMER_PENALTY = 0.10
NAME_MATCH_THRESHOLD = 80.0
UNIQUE_MARGIN = 0.05
TOP_N = 3


@dataclass(frozen=True)
class AlertView:
    id: int
    amount_paise: int
    received_at: datetime
    payer_name: str | None = None
    ref_id: str | None = None


@dataclass(frozen=True)
class BasketView:
    id: uuid.UUID
    total_paise: int
    ref_time: datetime  # when it started awaiting payment (or last activity if still open)
    status: str = "awaiting_payment"
    upi_ref_id: str | None = None
    customer_id: int | None = None


@dataclass(frozen=True)
class CustomerView:
    id: int
    aliases: tuple[str, ...]
    udhaar_balance_paise: int = 0


@dataclass
class ScoredCandidate:
    basket_id: uuid.UUID
    tier: int
    confidence: float
    reasons: list[str] = field(default_factory=list)
    total_paise: int = 0
    customer_id: int | None = None
    minutes_away: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "basket_id": str(self.basket_id),
            "tier": self.tier,
            "confidence": round(self.confidence, 4),
            "reasons": self.reasons,
            "total_paise": self.total_paise,
            "customer_id": self.customer_id,
            "minutes_away": round(self.minutes_away, 2),
        }


Outcome = Literal["auto_match", "auto_udhaar_payment", "pending_confirm"]


@dataclass
class Decision:
    outcome: Outcome
    candidates: list[ScoredCandidate]
    best: ScoredCandidate | None = None
    suggested_customer_id: int | None = None  # udhaar repayment suggestion
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "best": self.best.as_dict() if self.best else None,
            "candidates": [c.as_dict() for c in self.candidates],
            "suggested_customer_id": self.suggested_customer_id,
            "reason": self.reason,
        }


def name_score(payer: str | None, aliases: tuple[str, ...]) -> float:
    if not payer or not aliases:
        return 0.0
    return max(
        fuzz.token_set_ratio(payer, a, processor=utils.default_process) for a in aliases
    )


def best_customer_for_payer(
    payer: str | None, customers: list[CustomerView]
) -> tuple[CustomerView | None, float]:
    best, best_score = None, 0.0
    for c in customers:
        s = name_score(payer, c.aliases)
        if s > best_score:
            best, best_score = c, s
    return (best, best_score) if best_score >= NAME_MATCH_THRESHOLD else (None, best_score)


def _clamp(x: float) -> float:
    # Rounded so float drift (0.75 + 0.10 - 0.05 = 0.7999…) can't flip a threshold decision.
    return round(max(0.0, min(1.0, x)), 4)


def score_candidates(
    alert: AlertView,
    baskets: list[BasketView],
    customers: list[CustomerView],
    window_minutes: float,
) -> list[ScoredCandidate]:
    by_id = {c.id: c for c in customers}
    payer_customer, _ = best_customer_for_payer(alert.payer_name, customers)
    alert_ref = alert.ref_id.upper() if alert.ref_id else None

    def minutes_away(b: BasketView) -> float:
        return abs((alert.received_at - b.ref_time).total_seconds()) / 60.0

    amount_in_window = {
        b.id for b in baskets
        if b.total_paise == alert.amount_paise and minutes_away(b) <= window_minutes
    }
    n_same = len(amount_in_window)

    scored: list[ScoredCandidate] = []
    for b in baskets:
        mins = minutes_away(b)
        reasons: list[str] = []
        if alert_ref and b.upi_ref_id and alert_ref == b.upi_ref_id.upper():
            tier, conf = 1, TIER1_CONFIDENCE
            reasons.append(f"ref {alert_ref} matches basket ref")
        elif b.id in amount_in_window:
            tier, conf = 2, TIER2_BASE
            reasons.append(f"exact amount, {mins:.1f} min away")
            if n_same == 1:
                conf += TIER2_UNIQUE_BONUS
                reasons.append("only basket with this amount")
            else:
                conf -= TIER2_PER_EXTRA_CANDIDATE * (n_same - 1)
                reasons.append(f"{n_same} baskets share this amount")
            if mins > TIER2_GRACE_MINUTES:
                conf -= TIER2_PER_MINUTE * (mins - TIER2_GRACE_MINUTES)
        else:
            tier, conf = 3, TIER3_BASE

        # Tier 3 evidence: payer name vs. this basket's customer.
        customer = by_id.get(b.customer_id) if b.customer_id is not None else None
        name_matches_basket = (
            customer is not None
            and name_score(alert.payer_name, customer.aliases) >= NAME_MATCH_THRESHOLD
        )
        if tier == 3 and not name_matches_basket:
            continue  # neither ref, amount nor name: not a candidate
        if name_matches_basket:
            conf += NAME_MATCH_BONUS
            reasons.append("payer name matches basket customer")
        elif payer_customer is not None and payer_customer.id != b.customer_id:
            conf -= NAME_OTHER_CUSTOMER_PENALTY
            reasons.append("payer name matches a different customer")

        if tier == 1:
            conf = max(conf, TIER1_CONFIDENCE)  # a ref match is never diluted by name noise
        scored.append(
            ScoredCandidate(
                basket_id=b.id, tier=tier, confidence=_clamp(conf), reasons=reasons,
                total_paise=b.total_paise, customer_id=b.customer_id, minutes_away=mins,
            )
        )
    scored.sort(key=lambda c: (-c.confidence, c.tier, c.minutes_away))
    return scored


def decide(
    alert: AlertView,
    baskets: list[BasketView],
    customers: list[CustomerView],
    *,
    window_minutes: float = 10,
    threshold: float = 0.80,
    reminder_refs: dict[str, int] | None = None,
) -> Decision:
    """reminder_refs maps a reminder's pay-link ref -> customer_id (tier-1 udhaar repayment)."""
    alert_ref = alert.ref_id.upper() if alert.ref_id else None
    if alert_ref and reminder_refs and alert_ref in reminder_refs:
        return Decision(
            outcome="auto_udhaar_payment",
            candidates=[],
            suggested_customer_id=reminder_refs[alert_ref],
            reason=f"ref {alert_ref} matches an udhaar reminder pay link",
        )

    candidates = score_candidates(alert, baskets, customers, window_minutes)
    top = candidates[:TOP_N]
    best = top[0] if top else None

    if best is not None:
        runner_up = top[1].confidence if len(top) > 1 else 0.0
        unique = best.confidence - runner_up >= UNIQUE_MARGIN
        if best.confidence >= threshold and unique:
            return Decision("auto_match", top, best, reason="confident unique match")
        reason = "below threshold" if best.confidence < threshold else "ambiguous best candidate"
    else:
        reason = "no candidate basket"

    suggested = None
    payer_customer, _ = best_customer_for_payer(alert.payer_name, customers)
    if payer_customer is not None and payer_customer.udhaar_balance_paise > 0:
        suggested = payer_customer.id
        reason += "; payer has an udhaar balance"
    return Decision("pending_confirm", top, best, suggested_customer_id=suggested, reason=reason)
