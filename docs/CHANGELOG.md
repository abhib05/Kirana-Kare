# Changelog

## Real-API voice check: calibration swallowed the first sale
- Streamed synthesized speech ("Kirana Kare shuru karo. Do kilo atta, aur ek packet doodh. Bas, GPay karta hoon.") through `/ws/audio` via the frontend proxy to the real Muse service. Muse returned it as one turn (turn ids start at 0): `Kiranakir shurukaro do kilo atta or ek packet doodh business ji pay card hun`.
- Bug: the relay treated any turn *containing* the calibration phrase as calibration and dropped it, so the sale in the same turn was lost. Now `split_calibration()` cuts the phrase out, sets the merchant label, and processes the rest when it has at least two words.
- After the fix the same audio produced a basket awaiting UPI: ₹120, 2 kg atta + 1 packet milk (the LLM read "business ji pay" as GPay).
- Tests: the verbatim real transcript as a unit case; the fake Muse server now numbers turns from 0 and can merge the phrase into the first sale.
- APScheduler jobs get a 60 s misfire grace: Docker Desktop's VM clock jumps of 2–5 s were logging "missed" runs every minute.

## Real-API checks (after the key was added)
- Muse Voice Transcribe: real handshake, 1 s of streamed audio, `endStream`, close 1000. Observed that a `speaker` event can arrive after the first partial; still within the turn, so labels attach correctly. Silence can yield a `speechComplete` with an empty transcript, which the relay already drops.
- LLM extraction eval, first run: items 100%, but customers 0% and `action` mostly `"none"`, because the model echoed the schema's defaults. It also turned "500 rupaye" into 5,000,000 paise, which the ₹1 lakh bound would have let into the ledger.
- Fixes: `llm_json_schema()` sends every field as required with descriptions and no defaults; new `check_amounts_grounded()` rejects any repayment or price that matches no number spoken in the turn (±₹1), in both extractor backends. 7 regression tests, including the exact 100× output.
- LLM eval after the fix (40 typed turns): items P/R 100%/100%, qty 100%, payment mode 97.5%, customer 100%, chit-chat vs sale 97.5%. The one miss ("venkat anna ka hisaab baad mein") is left as is rather than tuning the prompt to the test set.

## Phase 9 — Polish
- README: problem, eval numbers, 5-minute quick start, demo script, architecture, design choices, tests, privacy, limitations, future work.
- Clean-clone check: `git clone` → `cp .env.example .env` → `docker compose up --build` → typed sale → UPI awaiting with ref → alert → ledger + stock update, all through the frontend proxy. It found a first-run bug: `.env.example` had inline comments, which Compose reads as part of the value, so `MODEL_API_KEY` became the comment text and every turn was rejected with an LLM auth error. Comments now sit on their own lines.
- GitHub Actions CI is green on `main`.
- Not done: demo GIF (needs a real voice session to be worth showing) and the optional Cloud Run deploy.

## Phase 8 — Evaluation & hardening
- `eval/generate_scenarios.py`: seeded synthetic shop-days with ground truth — rush hours, 0–8 min alert delays (6% early), refs echoed 40% of the time, bank/VPA/alias/initial payer names, same-amount twins, unpaid baskets, open cash baskets, UPI udhaar repayments, stray credits (half at common basket amounts), ~6% duplicate deliveries, and 7% of UPI sales never captured by the listener.
- `eval/run_eval.py`: replays scenarios through the production `ingest_alert` / `reconcile_alert` / `rereconcile_pending` on a dedicated `kirana_eval` database; 15-config grid (threshold × window) on the dev split, held-out scored with the dev pick and with the defaults; writes `eval/REPORT.md` + `eval/results.json` (`--report-only` rebuilds the report).
- Results (held-out, defaults 0.80 / ±10 min): 80.66% of 667 alerts auto-matched (90.54% of alerts for a captured sale) at 99.44% precision, 3 wrong; 100% of 51 duplicates ignored; no alert posted twice. The dev-tuned 0.70 / ±15 fell to 98.9% precision on held-out, so the defaults are kept.
- A first run had 0 wrong auto-matches at every setting because nothing in the scenarios could cause one; the missed-sale and common-amount stray cases were added before reporting.
- `eval/run_extraction_eval.py` + 40 hand-labelled code-mixed turns: rule baseline item P/R 97.7% / 95.5%, payment mode 100%, customer 100%. The eval found a rules bug (honorific taken as the customer name in "irfan bhai ke khate mein"), fixed with a regression test. `eval/transcribe_clips.py` streams recorded WAV clips through the same Muse session code for the end-to-end number.
- CI workflow (ruff, mypy, pytest with a 90% gate on the core packages, Vitest, frontend build); `docs/ARCHITECTURE.md`; decisions D4–D11.

## Phase 7 — Inventory, restock & reminders
- `close_basket` now calls `inventory.on_basket_closed` directly, so the sale, its ledger entry, the stock decrement (row-locked products) and any restock draft commit in one transaction. Lines in a unit that couldn't be converted don't touch stock; stock may go negative (that means the count was wrong, not that the sale didn't happen).
- `app/restock/drafts.py`: one open draft per vendor; suggested qty = ⌈2 × reorder − stock⌉ (multiplier configurable, minimum 1); bilingual message; `wa.me` link. New low items update the open draft; quantities the merchant edited are kept; items in a draft marked *sent* in the last 2 days count as on order. Edit / sent / dismiss endpoints, plus `POST /api/products/{id}/receive` for incoming stock.
- `app/notify/whatsapp.py`: phone normalization, `wa.me` links; optional Cloud API sender behind `WHATSAPP_CLOUD_API_ENABLED` (+ `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`).
- `app/reminders/jobs.py`: daily job (10:00 IST) drafts a reminder for balances above the threshold with no payment for N days (counted from the first udhaar sale if never paid), at most one open/recent reminder per customer. Each reminder has its own `KK…` ref in a `upi://pay` link, so paying through it is a tier-1 udhaar repayment. "Remind" button drafts one immediately and supersedes older drafts. APScheduler also runs the basket idle sweep every 60 s (`SCHEDULER_ENABLED=false` in tests).
- Frontend: **Restock** page (editable quantities, message preview, *Open in WhatsApp*, *Mark sent*, *Dismiss*); Udhaar page gains *Remind* and a reminders-to-send list.
- Tests (226 backend): stock decrements on cash and on UPI match (not before payment), unconvertible units untouched, a 31 kg sale creates the Sharma Wholesale draft with an 11 kg suggestion and a decodable `wa.me` link, drafts update rather than duplicate and keep edits, sent items aren't re-requested, reminder job threshold / N-day / no-phone / no-duplicate rules, paying through a reminder's link clears the balance, Cloud API via `httpx.MockTransport`.
- Verified in the browser: selling 31 kg atta shows the vendor draft and WhatsApp link on the Restock page.

## Phase 6 — Dashboard
- `EventsProvider` keeps one `/ws/events` socket (exponential reconnect); `useLive()` refetches a page's data on every server event, so all open dashboards stay in sync.
- Pages (hash-routed): **Live** (live transcript, open basket, awaiting-payment baskets with UPI QR codes, "Paid cash" / "Show UPI QR" / cancel, customer picker for udhaar baskets waiting on a name, simulate-payment buttons with and without ref, paste-an-SMS, recent sales), **Listener**, **Confirm** (each alert with top candidates, confidence bars, tier reasons, and one-tap *This sale* / *Udhaar repayment* / *Not a sale*; tab badge shows the queue size), **Udhaar** (balances, last payment, record a cash repayment), **Inventory** (stock with low-stock highlight, flagged/unmapped lines with one-tap mapping), **Summary** (cash/UPI/udhaar totals for any IST day, payment-mix bar, auto vs merchant-confirmed vs pending alerts).
- Backend: `GET /api/summary?date=`, `GET /api/basket-items/unmapped`; manual udhaar payments and reversals now publish `ledger_updated`.
- Verified in the browser against the compose stack: typed turns → basket awaiting UPI with QR → simulated alert auto-matched; two same-amount baskets + a no-ref alert → confirm queue (40% each, reasons shown) → one tap → queue empty; Summary reflects ₹221 UPI, 2 auto + 1 merchant-confirmed.
- Tests: 198 backend (summary math incl. empty days, unmapped lifecycle).

## Phase 5 — Voice: browser capture + Muse relay
- Frontend `src/audio/`: AudioWorklet capture (`worklet.ts`), streaming linear resampler to 24 kHz + Float32→Int16 + 80 ms little-endian framer (`pcm.ts`, Vitest-covered incl. 44.1 kHz drift), `MicStreamer` that keeps sending (silent) frames while muted. Listener page: Start/Stop, Mute, status pill (Listening / Hearing speech / Reconnecting / Offline / calibrating), live partial transcript, recent turns with resulting baskets, and a typed-turn box.
- Backend `app/asr/events.py`: typed models for the documented events (`speechStart`, `transcript`, `speechEnd`, `speechComplete`, `speaker`, `audioProgress`, `error`, handshake ack); unknown or malformed events become `UnknownEvent` instead of failing.
- `app/asr/relay.py`: handshake-then-ack before audio; pacing caps the stream at 4 s ahead of real time; injected PCM silence when the browser goes quiet; `speaker` labels attached to the latest `speechStart` (the docs show they carry no `turnId`); `speechComplete` turns go through an ordered worker into the basket engine; per-session merchant calibration on "Kirana Kare shuru karo"; proactive rollover at 55 min with `endStream` + drain of the old session; exponential-backoff reconnects, longer after 1013 rate limits.
- `WS /ws/audio` (reports `offline` with a reason when `MODEL_API_KEY` is missing) and `WS /ws/events` (dashboard event stream from the bus).
- Tests: contract tests for every documented event shape; relay tests against a real local WebSocket server imitating Muse — handshake fields, diarized turns and calibration, a compressed soak (rollover every 0.4 s, ≥ 3 sessions) asserting every finalized turn, including the one drained after `endStream`, is handled exactly once; reconnect after an abrupt close; 1013 backoff; handshake rejection; silence keepalive; pacing math; handler failures don't stop the relay.
- **Not yet verified:** speaking a sale through the real Muse service, and the real 70-minute soak. Both need a `MODEL_API_KEY` and a microphone.

## Phase 4 — Baskets & extraction (text-first)
- `app/extraction/schemas.py`: `ExtractedItem` / `ExtractedTurn` (extra fields forbidden), `check_business_rules` (qty > 0, per-unit max, udhaar payment bounds, add/remove need items) and `check_price_band` (0.5×–3× catalog). Everything raises `ExtractionRejected`.
- `app/extraction/extractor.py`: `LLMExtractor` (Meta Model API via the OpenAI SDK, `temperature=0`, JSON-schema response format, last 6 turns + catalog in the prompt; any API error, non-JSON or schema failure is rejected, never guessed) and `RuleExtractor` (deterministic Hindi/Telugu/English number words, units, payment and close cues, honorific-based customer names). Falls back to rules when no API key is set. Prompt versioned in `prompts/v1.txt`; the version is stored on every basket item.
- `app/extraction/catalog_match.py`: RapidFuzz `token_set_ratio` over name + aliases; ≥ 85 maps, 70–85 maps + flags, < 70 unmapped. Subset ties between different products ("dal") are capped into the flagged band. Unit conversion g↔kg, ml↔l, dozen↔pcs, pcs↔packet.
- `app/customers/resolve.py`: honorific-stripped fuzzy match against names + aliases; ≥ 85 resolves, ties between customers don't.
- `app/baskets/engine.py`: Redis session state (`basket:{shop}:{id}`, `open_baskets:{shop}`, rolling `turns:{shop}`, per-shop lock) with Postgres as source of truth. Every line is matched and priced before anything is written, so one bad line rejects the turn. Cash closes on a close cue (or remembers "cash" intent), udhaar closes when the customer resolves (else waits with candidates), UPI goes to `awaiting_payment` with a `KK…` ref + `upi://pay` link and re-runs reconciliation for alerts that arrived early. Udhaar repayments by voice post to the ledger. Idle sweep moves stale open baskets to `awaiting_payment`. Rejections go to `audit_log`.
- Migration `0002`: `baskets.last_activity_at`, `baskets.upi_link`; reconciliation uses last activity as the time reference for still-open baskets.
- API: `POST /api/dev/turn`, `GET /api/baskets[?status=]`, `GET /api/baskets/{id}`, `POST /api/baskets/{id}/close|cancel`, `POST /api/basket-items/{id}/map` (adds the spoken hint as a product alias), `GET/POST /api/products`, `GET/POST /api/vendors`.
- Tests (182 total): 12 golden typed conversations (cash, UPI, udhaar known/unknown, remove, spoken price, Telugu, chit-chat, implausible price, voice repayment, grams→kg, back-to-back sales) checked down to ledger rows; 10 malformed LLM outputs rejected; end-to-end text → UPI alert → ledger.

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
