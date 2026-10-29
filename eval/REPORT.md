# Kirana Kare — Reconciliation evaluation

Generated 2026-10-29 14:22 UTC · scenarios seed 7 · 40 synthetic shop-days
(20 dev / 20 held-out) · engine: production `reconcile_alert`
+ `rereconcile_pending` on PostgreSQL.

**What is simulated.** Each day has ~80 baskets (≈42% UPI, 43% cash, 15% udhaar) over 12 hours,
75% of them inside a morning (90 min) or evening (2 h) rush.
UPI alerts arrive 0–8 min after the basket awaits payment (6% arrive *before* it), echo the
basket's `KK…` ref only 40% of the time, carry payer names in bank/VPA/alias/initial styles or
none, ~1 in 12 UPI baskets has a same-amount twin within 2.5 min, 5% of UPI baskets are never
paid, and each day adds UPI udhaar repayments, stray credits (half at common basket amounts)
and ~6% duplicate deliveries. Open cash baskets add same-amount noise. **7% of UPI sales are
never captured by the listener**: their payments have no basket, so any auto-match of one is a
wrong match. This is the main source of wrong auto-matches. These are synthetic numbers: they measure the engine's
decision logic under controlled noise, not real-world payment-app behaviour.

## Held-out results
### Tuned on dev (precision floor 99.0%)
Config: threshold **0.70**, window **±15 min**

| Metric | Value |
|---|---|
| UPI alerts (unique) | 667 (592 for a basket) |
| Auto-match rate (all alerts) | **81.86%** |
| Auto-match rate (alerts for a sale) | 91.55% |
| Auto-match precision | **98.9%** |
| Wrong auto-matches | **6** (0.9% of alerts) |
| Sent to merchant confirmation | 18.14% |
| Correct basket in confirm-queue top 3 | 92.0% |
| Udhaar repayment suggested correctly | 100.0% |
| Stray credits auto-matched | 0 |
| Payments for sales the listener missed | 39, of which auto-matched (wrongly) 4 |
| Duplicate deliveries ignored | 100.0% of 51 |
| Alerts posted to the ledger more than once | 0 |
| With ref echoed: auto-match / precision | 99.6% / 99.6% (n=250) |
| Without ref: auto-match / precision | 85.67% / 99.66% (n=342) |
| Auto-matches by tier | tier 1: 237, tier 2: 309 |

### Plan defaults
Config: threshold **0.80**, window **±10 min**

| Metric | Value |
|---|---|
| UPI alerts (unique) | 667 (592 for a basket) |
| Auto-match rate (all alerts) | **80.66%** |
| Auto-match rate (alerts for a sale) | 90.54% |
| Auto-match precision | **99.44%** |
| Wrong auto-matches | **3** (0.45% of alerts) |
| Sent to merchant confirmation | 19.34% |
| Correct basket in confirm-queue top 3 | 94.64% |
| Udhaar repayment suggested correctly | 100.0% |
| Stray credits auto-matched | 0 |
| Payments for sales the listener missed | 39, of which auto-matched (wrongly) 2 |
| Duplicate deliveries ignored | 100.0% of 51 |
| Alerts posted to the ledger more than once | 0 |
| With ref echoed: auto-match / precision | 99.6% / 99.6% (n=250) |
| Without ref: auto-match / precision | 83.92% / 100.0% (n=342) |
| Auto-matches by tier | tier 1: 238, tier 2: 300 |

## Dev-split grid (used for tuning only)

| Threshold | Window (min) | Auto-match rate | Precision | Wrong auto | To merchant |
|---|---|---|---|---|---|
| 0.70 | 5 | 81.95% | 99.29% | 4 | 18.05% |
| 0.70 | 10 | 82.39% | 99.29% | 4 | 17.61% |
| 0.70 | 15 | 82.68% | 99.3% | 4 | 17.32% |
| 0.75 | 5 | 81.22% | 99.28% | 4 | 18.78% |
| 0.75 | 10 | 81.51% | 99.29% | 4 | 18.49% |
| 0.75 | 15 | 81.8% | 99.29% | 4 | 18.2% |
| 0.80 | 5 | 80.06% | 99.64% | 2 | 19.94% |
| 0.80 | 10 | 80.35% | 99.64% | 2 | 19.65% |
| 0.80 | 15 | 80.64% | 99.64% | 2 | 19.36% |
| 0.85 | 5 | 76.86% | 99.62% | 2 | 23.14% |
| 0.85 | 10 | 77.0% | 99.62% | 2 | 23.0% |
| 0.85 | 15 | 77.29% | 99.62% | 2 | 22.71% |
| 0.90 | 5 | 54.88% | 100.0% | 0 | 45.12% |
| 0.90 | 10 | 55.17% | 100.0% | 0 | 44.83% |
| 0.90 | 15 | 55.31% | 100.0% | 0 | 44.69% |

Selection rule: highest auto-match rate on dev among configs with auto-match precision
≥ 99.0%, ties broken toward the stricter threshold.

## Reading the numbers
- **The dev-tuned config did not hold up:** it met the 99.0% precision floor on dev but scored 98.9% on held-out (6 wrong), while the plan defaults kept 99.44% (3 wrong) for 1.2 points less auto-matching. The defaults stay in production; the dev margin over the floor was too thin to trust.
- A *wrong auto-match* books a payment against the wrong sale without asking; that is the costly
  failure, so precision is the constraint and auto-match rate is what's maximised under it.
- Alerts not auto-matched are not lost: they wait in the confirm queue with ranked candidates.
- Extraction accuracy (speech → items) is reported separately in `EXTRACTION_REPORT.md`.

Reproduce: `python eval/generate_scenarios.py --days 40 --seed 7 && python eval/run_eval.py`
