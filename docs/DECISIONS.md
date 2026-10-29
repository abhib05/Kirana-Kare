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
- `baskets.last_activity_at`, `baskets.upi_link` (migration 0002).

## D4. SMS idempotency key uses the UTR alone when present
The plan says hash(amount, ref/UTR, minute bucket). A UTR is already unique per payment, and
many SMS formats carry only a date, so the "minute" would be the paste time and a re-paste a
minute later would double count. With a UTR the key is hash(amount, UTR); without one it is
hash(amount, payer, minute).

## D5. Date-only SMS stamps are not used as the receive time
"on 02-11-26" parses to midnight, which would put the alert hours outside the match window.
Only stamps with a time of day are used; otherwise the alert is timed at ingestion.

## D6. Speaker labels attach to the latest speechStart
The Muse docs show `speaker` events carry `label` and `audioProcessedMs` but no `turnId`, so a
label is assigned to the most recent `speechStart`. Correctness never depends on it: labels
only feed the extractor context and the per-session merchant calibration.

## D7. Confidence rounding
Confidences are rounded to 4 dp, because 0.75 + 0.10 − 0.05 is 0.7999… in floating point,
which would silently turn the plan's "3 minutes away is still ≥ 0.80" into a confirm.

## D8. A cash mention without a close cue is intent, not a close
"Ek kilo pyaaz, cash dunga" records cash as the basket's intended mode; the basket closes on
the next close cue ("bas", "ho gaya"), so items added in between are not lost. UPI and udhaar
statements act immediately (UPI needs the ref/QR now; udhaar needs the customer now).

## D9. Rule-based extractor alongside the LLM
CI must not call Meta, and a demo should work without a key, so a deterministic extractor
implements the same `Extractor` interface. It is also the baseline the LLM is measured against.

## D10. Inventory is called directly from close_basket
An early hook list was replaced by a direct call: there is exactly one consumer, and an
explicit call makes it obvious that stock moves in the same transaction as the ledger.

## D11. In-process event bus
One backend process serves the MVP, so `/ws/events` is fed by an in-process bus. Multiple
replicas would need Redis pub/sub instead.
