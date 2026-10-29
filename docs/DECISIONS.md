# Design decisions

Short records of choices that deviate from, or fill gaps in, `KIRANA_KARE_PLAN.md`.

## D1. Sync SQLAlchemy behind FastAPI
FastAPI runs sync handlers in a threadpool; sync SQLAlchemy + psycopg 3 keeps the ledger and
reconciliation code simple to read and test. Only the WebSocket relay is async.

## D2. Postgres for DB tests, fakeredis for Redis
The append-only trigger and the balance views are Postgres features, so the tests that cover
them run against a real Postgres (`kirana_test` database created by `infra/postgres-init`).
Redis behaviour is simple enough that `fakeredis` is a faithful stand-in.

## D3. Extra columns beyond §4
- `baskets.upi_ref_id`, `baskets.awaiting_since`, `baskets.customer_name_hint`.
- `basket_items.product_hint`, `basket_items.flagged`, `basket_items.prompt_version`.
- `upi_alerts.candidates` (JSONB): the scored candidates from the last reconciliation pass,
  so the confirm queue shows exactly what the engine saw.
- `matches.kind` (`sale` / `udhaar_payment` / `not_a_sale`) and `matches.customer_id`, so an
  alert resolved as an udhaar repayment or "not a sale" is still recorded exactly once.
- `reminders.ref_id`: each reminder's pay link carries its own ref, which gives a tier-1 match.
