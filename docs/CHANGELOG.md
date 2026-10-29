# Changelog

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
