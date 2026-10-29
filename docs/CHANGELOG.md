# Changelog

## Phase 3 — Reconciliation engine
- `app/reconciliation/scoring.py` (pure): tier 1 ref match (0.99), tier 2 exact amount in ±window (0.75, +0.10 unique, −0.25 per extra same-amount basket, −0.05/min beyond 2 min), tier 3 payer-name evidence (+0.15 / −0.10 for a different known customer; name-only candidates start at 0.35). Auto-match needs ≥ threshold **and** a unique best (≥ 0.05 margin). Confidences are rounded to 4 dp so float drift can't flip a threshold. Udhaar-repayment suggestion when the payer owes money; a reminder's pay-link ref is a tier-1 udhaar repayment.
- `app/reconciliation/engine.py`: alert row `FOR UPDATE` + basket row `FOR UPDATE` re-check + `matches.alert_id UNIQUE`; match, basket close, ledger post and audit entry commit together. Merchant decisions (`basket` / `udhaar_payment` / `not_a_sale`) learn the confirmed payer name as a customer alias. `rereconcile_pending` retries alerts that arrived before their basket was awaiting payment.
- `app/baskets/closing.py`: the single function that closes a basket and posts the sale (with an on-close hook list for inventory in Phase 7).
- `app/upi/links.py`: `KK` + 6 base32 ref IDs and `upi://pay` links.
- `app/events.py`: thread-safe in-process event bus for the upcoming `/ws/events`.
- API: alerts are reconciled on ingest; `GET /api/confirm-queue` (top-3 candidates with basket summaries, udhaar suggestion); `POST /api/matches/{alert_id}/decide`.
- Tests (120 total): 18-row decision table plus edge cases (ties, three-way ties, late alerts, off-by-one-paisa, different-customer payer, clamping, top-3); DB tests for 10 concurrent alerts racing for one basket and 8 workers reconciling the same alert — exactly one match and one ledger entry each time. Coverage: scoring 100%, engine 98%, ledger 99%, alerts 94–97%.

## Phase 2 — Ledger, money, and alert ingestion
- `app/money.py`: paise helpers (`rupees_to_paise` half-up, rejects floats/bools/NaN; `format_inr`, `upi_amount`, `line_total`).
- `app/ledger/service.py`: the only writer of `ledger_entries` — `post()` validates type/amount/customer, `reverse()` creates a `reversal` entry (no double reversal, no reversing a reversal). Flushes only; callers own the transaction.
- `app/udhaar/service.py`: `record_payment()` for cash (or UPI-assigned) udhaar repayments.
- `POST /api/alerts`: `INSERT … ON CONFLICT (idempotency_key) DO NOTHING`; a duplicate returns 200 with the original alert and `duplicate: true`.
- `POST /api/alerts/parse-sms`: regex parser for 9 bank/app credit formats (HDFC, SBI, ICICI, Axis, Kotak, BHIM, PhonePe, GPay, Paytm) plus echoed `KK…` refs. Debit messages are rejected. Idempotency key = hash(amount, UTR), or hash(amount, payer, minute) when there is no UTR. Date-only SMS stamps are not used as the receive time (they would break the match window).
- `GET /api/customers`, `GET /api/udhaar`, `POST /api/udhaar/{id}/payment`, `GET /api/ledger`, `POST /api/ledger/{id}/reverse`.
- Tests (72 total): hypothesis property test runs random sequences of sales, payments and reversals against the real `udhaar_balances` view and checks it equals an in-memory model, that only the named customer's balance moves, and that the sum of balances equals the net udhaar effect of all entries. 16 concurrent duplicate deliveries insert exactly once.

## Phase 1 — Skeleton & data layer
- Repo layout per the plan: `backend/` (FastAPI, uv), `frontend/` (Vite + React + TS + Tailwind), `docs/`, `infra/`.
- `docker compose up` runs Postgres 16, Redis 7, backend (migrates + seeds on start) and the frontend dev server (proxies `/api`, `/ws`, `/healthz` to the backend).
- SQLAlchemy 2 models for every table in §4; all money columns are `BIGINT` paise; every table carries `shop_id`.
- Alembic migration `0001` creates the schema plus raw DDL from `app/db/sql.py`:
  - `trg_ledger_entries_append_only` rejects `UPDATE`/`DELETE` on `ledger_entries`.
  - Views `udhaar_balances` and `daily_summary` (reversals carry the negated effect of the entry they reverse).
  - Constraints: positive amounts, `reversal ⇔ reverses_entry_id`, an entry can be reversed at most once, unique alert idempotency keys.
- Seed: demo shop, 40 products with Hindi/Telugu/English aliases, 3 vendors, 10 customers with spoken and UPI-payer aliases.
- Tests: `tests/integration/test_schema.py` (seed counts, trigger blocks UPDATE/DELETE, check constraints, unique idempotency key). DB tests use the separate `kirana_test` database and are skipped if Postgres is not reachable.
