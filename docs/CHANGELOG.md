# Changelog

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
