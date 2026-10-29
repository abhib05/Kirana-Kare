# Kirana Kare — Build Spec & Implementation Plan

> **The ledger that listens.** A hardware-free, voice-first bookkeeping and payment-reconciliation agent for Indian kirana (neighbourhood grocery) merchants.
>
> **How to use this file with Claude Code:** put it in the repo root (you can also copy sections into `CLAUDE.md`). Ask Claude Code to build **one phase at a time** ("Implement Phase 2 from KIRANA_KARE_PLAN.md"), and do not move on until that phase's acceptance criteria pass. Review every diff yourself — you must be able to explain every line in an interview.

---

## 0. Ground rules for the coding agent

1. **Build phase by phase.** Each phase ends with passing tests and a short note in `docs/CHANGELOG.md`.
2. **Tests first for core logic.** The reconciliation engine, ledger, and extraction validation must have pytest coverage ≥ 90%.
3. **Money is integers.** All amounts are stored and computed in **paise** (`int`), never floats. ₹150.50 → `15050`.
4. **The ledger is append-only.** Never `UPDATE` or `DELETE` a ledger entry. Corrections are new reversing entries. Balances are derived.
5. **Everything external is idempotent.** Every incoming UPI alert carries an idempotency key; processing the same alert twice must not double-count.
6. **No secrets in code or in the browser.** API keys live in `.env` (gitignored) and are read only by the backend. The browser never talks to Meta directly.
7. **Raw audio is never stored.** Audio is streamed, transcribed, and discarded. Only extracted transactions reach the database.
8. **Validate every LLM output.** LLM responses are parsed into Pydantic models; anything invalid is rejected and logged, never written to the ledger.
9. **Keep it runnable locally** with `docker compose up`. Cloud deployment is optional (Phase 9).

---

## 1. Problem & scope

**Problem.** A kirana merchant's UPI soundbox says "₹150 received" but never *what* it was for. Credit (*udhaar*) lives in a paper notebook (*khata*). Restocking is guesswork. Cash, UPI, and credit sales are never recorded together.

**Solution.** A browser-based "counter listener" streams the counter conversation to speech-to-text. An LLM extracts items, quantities, and payment intent into an open **basket**. When a UPI alert arrives, a **reconciliation engine** matches it to the right basket. Cash and credit sales close by voice. Every sale lands in a PostgreSQL **ledger**; credit balances, inventory, reminders, and restock drafts are derived from it.

### In scope (MVP for the resume)
- Browser microphone capture → backend relay → Meta **Muse Voice Transcribe** (realtime, diarization).
- LLM transaction extraction (items, qty, price, payment mode, customer name, udhaar intent).
- Basket session engine (Redis).
- UPI alert ingestion: **simulated alert API** + **paste-an-SMS/notification parser**.
- Three-tier reconciliation engine with confidence scoring and merchant confirmation queue.
- Append-only ledger: sales, payments, udhaar (credit) per customer.
- Inventory decrement + low-stock detection + **vendor restock message drafts** (one-tap send).
- Udhaar reminders with UPI pay links (drafted for the merchant to send).
- React + Tailwind merchant dashboard (live feed, confirm queue, udhaar book, inventory, restock).
- Evaluation harness that produces real accuracy numbers for the resume.

### Out of scope (future work — mention in README only)
- Raspberry Pi / any hardware listener.
- Android notification-listener app for PhonePe/GPay/Paytm alerts.
- Real PSP/UPI integration, GST reports, demand forecasting, lending on verified cash flow.
- Multi-tenant auth beyond a single demo merchant (keep the schema multi-tenant-ready with `shop_id`).

---

## 2. Architecture

```
 ┌──────────────── Browser (React + Tailwind) ────────────────┐
 │  Counter Listener page        Merchant Dashboard            │
 │  - AudioWorklet mic capture   - live transaction feed       │
 │  - resample → PCM16 @24kHz    - confirm queue (one tap)     │
 │  - WS binary frames           - udhaar book / inventory     │
 └──────────────┬──────────────────────────┬──────────────────┘
                │ WS /ws/audio             │ WS /ws/events + REST
 ┌──────────────▼──────────────────────────▼──────────────────┐
 │                  FastAPI backend (Python)                   │
 │  ASR relay ──► Muse Voice Transcribe (wss, DIARIZATION)     │
 │      │ speechComplete turns                                  │
 │      ▼                                                       │
 │  Extractor (LLM, structured JSON) ──► Pydantic validation   │
 │      ▼                                                       │
 │  Basket session engine (Redis) ◄── Alert ingestion (API/SMS)│
 │      ▼                                                       │
 │  Reconciliation engine (3 tiers + confidence)                │
 │      ▼                                                       │
 │  Ledger service (append-only, idempotent) ──► PostgreSQL     │
 │      ▼                                                       │
 │  Inventory · Udhaar · Reminders · Restock (APScheduler)      │
 │      ▼                                                       │
 │  Notifier: WhatsApp drafts (wa.me links / Cloud API opt.)    │
 └──────────────────────────────────────────────────────────────┘
```

### Tech stack
| Layer | Choice |
|---|---|
| Frontend | React (Vite, TypeScript), Tailwind CSS |
| Backend | Python 3.11+, FastAPI, Uvicorn, `websockets`, Pydantic v2 |
| Speech-to-text | Meta Muse Voice Transcribe (`muse-voice-transcribe-1.0`), realtime WebSocket |
| LLM | Meta Model API (OpenAI-compatible, `base_url=https://api.meta.ai/v1`), model configurable (default `muse-spark-1.3`), structured output |
| Fuzzy matching | RapidFuzz |
| Database | PostgreSQL (Supabase or local Docker), SQLAlchemy 2 + Alembic |
| Cache / sessions | Redis |
| Scheduling | APScheduler |
| Messaging | WhatsApp `wa.me` deep links (default); WhatsApp Business Cloud API (optional) |
| Testing | pytest, pytest-asyncio, hypothesis (property tests), Vitest (frontend, light) |
| Tooling | Docker Compose, GitHub Actions CI, ruff, mypy |

---

## 3. Repository structure

```
kirana-kare/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app, routers, lifespan
│   │   ├── config.py               # pydantic-settings, reads .env
│   │   ├── db/                     # SQLAlchemy models, session, alembic
│   │   ├── asr/
│   │   │   ├── relay.py            # browser WS ↔ Meta realtime WS
│   │   │   └── events.py           # typed Muse event models
│   │   ├── extraction/
│   │   │   ├── extractor.py        # LLM call + schema
│   │   │   ├── schemas.py          # ExtractedTurn, ExtractedItem
│   │   │   └── catalog_match.py    # RapidFuzz item → SKU mapping
│   │   ├── baskets/engine.py       # open/update/close baskets in Redis
│   │   ├── alerts/
│   │   │   ├── ingest.py           # POST /alerts, idempotency
│   │   │   └── sms_parser.py       # parse pasted bank/UPI SMS text
│   │   ├── reconciliation/
│   │   │   ├── engine.py           # 3-tier matcher
│   │   │   └── scoring.py          # confidence model
│   │   ├── ledger/service.py       # append-only postings, balances
│   │   ├── inventory/service.py
│   │   ├── udhaar/service.py
│   │   ├── reminders/jobs.py       # APScheduler jobs
│   │   ├── restock/drafts.py       # vendor message drafts
│   │   ├── notify/whatsapp.py      # wa.me links / Cloud API
│   │   ├── upi/links.py            # upi://pay deep links with ref IDs
│   │   └── api/                    # REST + WS routers
│   ├── tests/
│   │   ├── unit/                   # reconciliation, ledger, parser, scoring
│   │   ├── integration/            # API + DB + Redis
│   │   └── fixtures/               # golden transcripts, sample SMS
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── audio/worklet.ts        # AudioWorklet processor
│   │   ├── audio/pcm.ts            # resample + Float32→Int16
│   │   ├── pages/Listener.tsx
│   │   ├── pages/Dashboard.tsx
│   │   └── components/…
│   └── package.json
├── eval/
│   ├── generate_scenarios.py       # synthetic shop-day simulator
│   ├── run_eval.py                 # reconciliation + extraction metrics
│   └── recordings/                 # your own test clips (gitignored audio)
├── docs/ (CHANGELOG.md, ARCHITECTURE.md, DECISIONS.md)
├── docker-compose.yml
├── .env.example
└── .github/workflows/ci.yml
```

---

## 4. Data model (PostgreSQL)

All money columns are `BIGINT` paise. All tables carry `shop_id` and `created_at TIMESTAMPTZ DEFAULT now()`.

```sql
shops(id, name, upi_vpa, language_bias TEXT[], created_at)

customers(id, shop_id, display_name, phone NULL, created_at)
customer_aliases(id, customer_id, alias TEXT, source TEXT)   -- spoken names, UPI payer names
  -- source ∈ {'voice','upi','manual'}

products(id, shop_id, name, aliases TEXT[], unit, price_paise, stock_qty NUMERIC,
         reorder_level NUMERIC, vendor_id NULL)
vendors(id, shop_id, name, phone, created_at)

baskets(id UUID, shop_id, customer_id NULL, status, total_paise, payment_mode NULL,
        opened_at, closed_at NULL, source_turn_ids INT[])
  -- status ∈ {'open','awaiting_payment','closed','cancelled'}
  -- payment_mode ∈ {'cash','upi','udhaar','mixed'}
basket_items(id, basket_id, product_id NULL, raw_text, qty NUMERIC, unit,
             unit_price_paise, line_total_paise, match_score REAL)

upi_alerts(id, shop_id, idempotency_key TEXT UNIQUE, amount_paise, payer_name NULL,
           ref_id NULL, received_at, raw_text, source, status)
  -- source ∈ {'simulated','sms_paste','webhook'}
  -- status ∈ {'unmatched','matched','pending_confirm','ignored'}

matches(id, alert_id UNIQUE, basket_id, tier SMALLINT, confidence REAL,
        decided_by TEXT, decided_at)
  -- decided_by ∈ {'auto','merchant'}

ledger_entries(id BIGSERIAL, shop_id, entry_type, amount_paise, customer_id NULL,
               basket_id NULL, alert_id NULL, reverses_entry_id NULL, memo, created_at)
  -- entry_type ∈ {'sale_cash','sale_upi','sale_udhaar','udhaar_payment','reversal'}
  -- APPEND-ONLY: enforce with a trigger that rejects UPDATE/DELETE.

restock_drafts(id, shop_id, vendor_id, items JSONB, message_text, wa_link,
               status, created_at)              -- status ∈ {'draft','sent','dismissed'}

reminders(id, shop_id, customer_id, amount_paise, pay_link, message_text,
          wa_link, status, due_at)

audit_log(id, shop_id, actor, action, payload JSONB, created_at)
```

**Derived views:**
- `udhaar_balances`: per customer, `SUM(sale_udhaar) − SUM(udhaar_payment) ± reversals`.
- `daily_summary`: cash vs UPI vs udhaar totals per day.

---

## 5. Component specifications

### 5.1 Counter Listener (frontend audio capture)
- Request the mic with `getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}})`.
- Use an **AudioWorklet** (not the deprecated ScriptProcessor) to receive Float32 frames at the device rate (usually 44.1/48 kHz).
- **Resample to 24 kHz**, convert Float32 [-1, 1] → **Int16 little-endian**, and send ~80 ms frames (1,920 samples = 3,840 bytes) as binary over `WS /ws/audio`.
- Keep sending frames during silence (the ASR service requires real-time pacing and closes idle streams).
- UI: big Start/Stop listening button, live partial transcript, status pill (Listening / Hearing speech / Reconnecting / Offline), mute button.

### 5.2 ASR relay (backend ↔ Muse Voice Transcribe)
Docs: <https://dev.meta.ai/docs/speech-to-text>

- Connect to `wss://api.meta.ai/v1/asr/realtime?sessionId=<uuid>`.
- **First text frame (within 10 s)** is the handshake:
  ```json
  {
    "authorization": {"accessToken": "Bearer <MODEL_API_KEY>"},
    "model": "muse-voice-transcribe-1.0",
    "audioEncoding": "PCM_24KHZ",
    "mode": "DIARIZATION",
    "partialMode": "CUMULATIVE",
    "languageBias": ["Hindi", "English", "Telugu"],
    "keywords": ["<top product names from the catalog>", "udhaar", "khata", "UPI"],
    "emitAudioProgress": false
  }
  ```
  `languageBias` and `keywords` come from the shop config and catalog; they cannot change mid-session.
- Wait for the handshake ack (`{"sessionId": ...}`) before forwarding audio.
- Forward browser binary frames as-is. Never run more than ~5 s ahead of real time.
- Handle events (key state on `turnId`, turns can overlap):
  - `transcript` (partial) → push to dashboard for live display only.
  - `speaker` → record the label (`A`, `B`, …) for the active turn.
  - `speechComplete` → **final turn text** → hand `{turnId, text, speaker, ts}` to the extractor.
  - `error` → log with session ID; reconnect with backoff where the close code allows.
- **Session rollover:** sessions last ≤ 60 minutes and cannot be resumed. Proactively open a new session at ~55 minutes, switch audio to it, then send `{"type":"endStream"}` on the old one and drain it until close `1000`.
- **Speaker labels are session-scoped, not identities.** MVP rule: when listening starts, the UI asks the merchant to say a fixed phrase ("Kirana Kare shuru karo"); the label on that turn is stored as the merchant for the session. Re-calibrate on every new session. The extractor also receives the label so it can reason about who said what, but correctness must not depend on it.
- Note the model provides **no confidence scores or word timestamps** — confidence in this system comes from the reconciliation engine, not the ASR.

### 5.3 Transaction extractor (LLM)
- Input: a short rolling context (last ~6 final turns with speaker labels) + the current turn + the shop's product catalog (names and aliases only, truncated to the top N by sales).
- Call the Meta Model API with **structured output** constrained to this schema:
  ```python
  class ExtractedItem(BaseModel):
      raw_text: str               # what was said, e.g. "do kilo atta"
      product_hint: str           # normalized name, e.g. "atta"
      qty: Decimal
      unit: Literal["kg","g","l","ml","pcs","packet","dozen"] | None
      spoken_price_paise: int | None

  class ExtractedTurn(BaseModel):
      is_transactional: bool       # False for chit-chat → discard
      items: list[ExtractedItem]
      payment_mode: Literal["cash","upi","udhaar","unknown"]
      customer_name: str | None    # e.g. "Ramesh bhaiya"
      action: Literal["add_items","remove_items","close_basket","udhaar_payment","none"]
      udhaar_payment_paise: int | None
  ```
- Validation pipeline: Pydantic parse → business checks (qty > 0, qty < sane max, price within 0.5×–3× catalog price) → reject + log on failure. **Never write unvalidated output.**
- Map `product_hint` → `products` with **RapidFuzz** (`token_set_ratio` against name + aliases). Score ≥ 85 auto-maps; 70–85 maps but flags the line; < 70 stays unmapped (`product_id NULL`) and appears in the dashboard for one-tap mapping, which also adds a new alias.
- Prompt is versioned in `extraction/prompts/v1.txt`; record the prompt version on each basket item for traceability.

### 5.4 Basket session engine (Redis)
- One **open basket** per conversation window. A transactional turn with `add_items` opens a basket if none is open, else appends.
- Basket state in Redis (`basket:{shop_id}:{id}` hash + `open_baskets:{shop_id}` sorted set by `opened_at`), persisted to Postgres on every change.
- Close triggers:
  - `payment_mode=cash` + `close_basket` → close as **cash** → ledger `sale_cash`.
  - `payment_mode=udhaar` → resolve customer (5.8) → close as **udhaar** → ledger `sale_udhaar`.
  - `payment_mode=upi` → status `awaiting_payment` → generate a UPI link with a ref ID (5.9) → wait for reconciliation.
  - Inactivity timeout (configurable, default 5 min) with no payment → `awaiting_payment` and surface in the dashboard.
- Totals: line total = qty × (spoken price if present else catalog price). All in paise.

### 5.5 UPI alert ingestion
- `POST /api/alerts` (simulated / webhook-style):
  ```json
  {"idempotency_key": "...", "amount_paise": 15000, "payer_name": "RAMESH K",
   "ref_id": "KK8F3A2", "received_at": "2026-11-02T10:31:05+05:30", "source": "simulated"}
  ```
  - Insert with `ON CONFLICT (idempotency_key) DO NOTHING`; a duplicate returns `200` with the original result.
- `POST /api/alerts/parse-sms`: accepts raw pasted text from a bank SMS or app notification, extracts amount, payer name, UPI ref, and timestamp with regexes (start with 6–10 real formats in fixtures; LLM fallback only if regexes fail, and still validated). Idempotency key = hash of (amount, ref/UTR, minute bucket).
- Every alert is pushed to the reconciliation engine.

### 5.6 Reconciliation engine (the Stripe-relevant core)
For each alert, find candidate baskets in `awaiting_payment` (and recently open ones) within the shop.

| Tier | Rule | Base confidence |
|---|---|---|
| 1 | Alert `ref_id` equals a basket's generated UPI ref | 0.99 |
| 2 | Amount equals basket total (exact paise) **and** basket closed/awaiting within the time window (default ±10 min) | 0.75 base, adjusted |
| 3 | Payer name fuzzy-matches a customer alias linked to a candidate basket (RapidFuzz ≥ 80) | adds evidence |

**Scoring (`scoring.py`)** — combine evidence into a calibrated score:
- Start from the highest tier matched.
- Tier 2 adjustments: +0.10 if exactly one candidate has that amount; −0.25 per additional candidate with the same amount; −0.05 per minute away from the basket close time beyond 2 min.
- Tier 3: +0.15 if payer name matches the basket's customer; −0.10 if it matches a *different* known customer.
- Clamp to [0, 1].

**Decision:**
- `confidence ≥ 0.80` and a unique best candidate → **auto-match** → ledger `sale_upi`, basket `closed`, `matches.decided_by='auto'`.
- Otherwise → `pending_confirm`; dashboard shows the alert with the top 3 candidate baskets; merchant taps one (or "not a sale" / "udhaar repayment") → `decided_by='merchant'`.
- An alert matching no basket but matching a customer with an udhaar balance → suggest **udhaar repayment**.
- **Exactly-once:** wrap match + ledger posting in one DB transaction; `matches.alert_id` is UNIQUE so an alert can never be matched twice.
- Every decision writes to `audit_log` (inputs, candidates, scores, outcome) so any match can be explained later.

### 5.7 Ledger service
- Single entry point `post(entry)`; no other code writes to `ledger_entries`.
- Corrections = `reversal` entries referencing `reverses_entry_id`; the original is untouched.
- DB trigger rejects UPDATE/DELETE on `ledger_entries`.
- Invariant tests (property-based with hypothesis): for any random sequence of sales, payments, and reversals, the sum of per-customer balances equals the total of udhaar entries net of payments and reversals, and no balance changes without a ledger entry.

### 5.8 Customer resolution (udhaar)
- Spoken name → RapidFuzz against `customer_aliases`. ≥ 85 → that customer. Lower → dashboard prompt: pick an existing customer or create a new one (the spoken name becomes an alias).
- UPI payer names are added as aliases when the merchant confirms a match, so the system improves with use.

### 5.9 UPI pay links
- Format: `upi://pay?pa=<shop VPA>&pn=<shop name>&am=<rupees>&cu=INR&tr=<ref_id>&tn=<short note>`.
- `ref_id`: short, unique, URL-safe (e.g. `KK` + 6 base32 chars), stored on the basket.
- Shown as a QR code on the dashboard and included in reminders. (Whether the payer's app echoes `tr` back in the alert varies; that's exactly why tiers 2 and 3 exist.)

### 5.10 Inventory & restock
- On basket close, decrement `stock_qty` for mapped items (in the same transaction as the ledger post).
- When `stock_qty ≤ reorder_level`, create/update a `restock_draft` grouped **per vendor**: a short, polite Hindi/English message listing items and suggested quantities (suggested qty = 2× reorder level − current stock, configurable).
- **One-tap send:** generate a `https://wa.me/<vendor phone>?text=<urlencoded message>` link. The merchant taps it on the dashboard and WhatsApp opens pre-filled; one more tap sends. This needs no WhatsApp Business verification. Optional: send via WhatsApp Business Cloud API behind a feature flag.
- Merchant can edit quantities before sending; marking sent updates the draft status.

### 5.11 Udhaar reminders
- APScheduler daily job: for customers with balance > threshold and no payment in N days, create a reminder with the amount, a UPI pay link (with ref ID → tier-1 match when paid), and a `wa.me` link to the customer's phone if known.
- The merchant sends; the system never messages customers automatically in the MVP.

### 5.12 Merchant dashboard (React)
Pages / panels:
1. **Live** — partial transcript, current open basket (items, qty, total), recent closed sales with mode badges (Cash / UPI / Udhaar).
2. **Confirm queue** — alerts needing a decision, each with top candidates and confidence; one tap to resolve.
3. **Udhaar book** — customers, balances, last payment, "send reminder" button.
4. **Inventory** — stock levels, low-stock highlights, unmapped items to map.
5. **Restock** — vendor drafts with one-tap WhatsApp buttons.
6. **Summary** — today's cash / UPI / udhaar totals, matched vs pending alerts.
Real-time updates via `WS /ws/events` (server pushes typed events: `partial`, `basket_updated`, `basket_closed`, `alert_received`, `match_decided`, `restock_draft`).

---

## 6. API surface (initial)

| Method | Path | Purpose |
|---|---|---|
| WS | `/ws/audio` | Browser → backend PCM stream |
| WS | `/ws/events` | Backend → dashboard live events |
| POST | `/api/alerts` | Ingest a (simulated) UPI alert, idempotent |
| POST | `/api/alerts/parse-sms` | Parse pasted SMS/notification text |
| GET | `/api/confirm-queue` | Pending alerts with candidates |
| POST | `/api/matches/{alert_id}/decide` | Merchant decision |
| GET/POST | `/api/baskets`, `/api/baskets/{id}` | View / manually adjust a basket |
| GET | `/api/customers`, `/api/udhaar` | Customers and balances |
| POST | `/api/udhaar/{customer_id}/payment` | Record a cash udhaar repayment |
| GET/POST | `/api/products`, `/api/vendors` | Catalog management |
| GET | `/api/restock-drafts` · POST `/api/restock-drafts/{id}/sent` | Restock flow |
| GET | `/api/summary?date=` | Daily totals |
| GET | `/healthz` | Liveness |

---

## 7. Evaluation harness (this produces your resume numbers)

You need **real, reproducible metrics** — never put the deck's "≥90%" target on the resume.

1. **Synthetic shop-day simulator** (`eval/generate_scenarios.py`): generates N realistic days — baskets with catalog items, a mix of cash / UPI / udhaar, UPI alerts with realistic noise (delays of 0–8 min, missing ref IDs ~60% of the time, payer names in different spellings/case, duplicate alert deliveries, two customers paying the same amount close together).
2. **Reconciliation eval** (`eval/run_eval.py`): feeds scenarios through the real engine and reports:
   - auto-match rate, auto-match **precision** (wrong auto-matches are the costly failure — target ≥ 99%),
   - share sent to merchant confirmation,
   - duplicate alerts correctly ignored (must be 100%).
3. **Extraction eval:** record 30–50 short counter-style clips yourself (and friends) in Hindi/Telugu/English code-mixed speech; hand-label the expected items/qty/mode in `tests/fixtures/golden_turns.jsonl`; report item-level precision/recall and payment-mode accuracy end to end (ASR + LLM).
4. Output a markdown report `eval/REPORT.md` with the numbers and the date. These numbers go on the resume and in the README.

---

## 8. Testing & CI

- **Unit:** reconciliation tiers and scoring (table-driven tests for every edge case: ties, duplicates, late alerts, same amount different customers), ledger invariants (hypothesis), SMS parser (fixtures), catalog matching, paise arithmetic, UPI link builder.
- **Integration:** FastAPI + Postgres + Redis via docker compose in CI; end-to-end "speech turn → basket → alert → match → ledger" using recorded event fixtures (no live Meta calls in CI — mock the ASR and LLM clients behind interfaces).
- **Contract tests** for Muse event parsing using the documented event shapes.
- **CI (GitHub Actions):** ruff, mypy, pytest with coverage report, frontend build. Block merges on failure.
- Coverage target: ≥ 90% on `reconciliation/`, `ledger/`, `alerts/`, `extraction/schemas.py`.

---

## 9. Phased build plan

Each phase: implement → tests green → update CHANGELOG → commit.

### Phase 1 — Skeleton & data layer
- Repo structure, docker compose (Postgres, Redis, backend, frontend), `.env.example`, config.
- SQLAlchemy models + Alembic migration for §4, append-only trigger on `ledger_entries`.
- Seed script: one demo shop, ~40 products with Hindi/English aliases, 3 vendors, 10 customers.
- **Accept:** `docker compose up` works; migrations apply; trigger blocks UPDATE/DELETE (tested).

### Phase 2 — Ledger, money, and alert ingestion
- Ledger service with reversals; udhaar balance view; paise utilities.
- `POST /api/alerts` with idempotency; SMS parser with fixtures.
- **Accept:** hypothesis invariant tests pass; duplicate alerts are no-ops; parser passes all fixtures.

### Phase 3 — Reconciliation engine (core)
- Tiers 1–3, scoring, decision logic, confirm queue API, audit log, UPI link + ref ID generator.
- **Accept:** table-driven tests cover all edge cases in §5.6; exactly-once guaranteed under concurrent alerts (test with parallel requests).

### Phase 4 — Baskets & extraction (text-first)
- Basket engine on Redis; extractor with structured output + validation; RapidFuzz catalog matching; customer resolution.
- Drive it with **typed text turns** first (a `POST /api/dev/turn` endpoint) so the pipeline works before audio exists.
- **Accept:** golden text turns produce correct baskets; invalid LLM output is rejected and logged.

### Phase 5 — Voice: browser capture + Muse relay
- AudioWorklet capture, resampling, PCM16 framing; `/ws/audio`; Muse realtime relay with handshake, diarization, `speechComplete` handling, merchant-label calibration, 55-minute rollover, reconnect with backoff, silence pacing.
- **Accept:** speak a sale into the browser → basket appears on the dashboard; a 70-minute soak test survives one session rollover without losing turns.

### Phase 6 — Dashboard
- Live, Confirm queue, Udhaar book, Inventory, Restock, Summary pages; `/ws/events`.
- **Accept:** full demo loop works from the UI: speak → basket → simulate UPI alert → auto-match or one-tap confirm → balances and stock update live.

### Phase 7 — Inventory, restock & reminders
- Stock decrement in the close transaction; restock drafts per vendor with `wa.me` links; APScheduler udhaar reminders with pay links.
- **Accept:** selling past the reorder level creates a vendor draft; tapping opens WhatsApp pre-filled; reminder job creates correct drafts.

### Phase 8 — Evaluation & hardening
- Scenario simulator, reconciliation eval, recorded-clip extraction eval, `eval/REPORT.md`.
- Tune thresholds (window, confidence cut-off) **on a dev split**, report on a held-out split.
- **Accept:** report generated with real numbers; wrong auto-match rate documented.

### Phase 9 — Polish & (optional) deploy
- README with architecture diagram, demo GIF, eval numbers, limitations, future work.
- Optional: Dockerize for Cloud Run; managed Postgres (Supabase) and Redis.
- **Accept:** a stranger can clone, set `.env`, run `docker compose up`, and complete the demo in 5 minutes.

---

## 10. Configuration (`.env.example`)

```
MODEL_API_KEY=            # Meta Model API key (backend only)
ASR_MODEL=muse-voice-transcribe-1.0
LLM_MODEL=muse-spark-1.3
LLM_BASE_URL=https://api.meta.ai/v1
DATABASE_URL=postgresql+psycopg://kk:kk@db:5432/kirana
REDIS_URL=redis://redis:6379/0
SHOP_LANGUAGE_BIAS=Hindi,English,Telugu
MATCH_WINDOW_MINUTES=10
AUTO_MATCH_THRESHOLD=0.80
BASKET_IDLE_TIMEOUT_SECONDS=300
WHATSAPP_CLOUD_API_ENABLED=false
```

---

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Code-mixed speech transcribes poorly | Keyword biasing with catalog names; language bias per shop; measure with the recorded-clip eval early (Phase 5), before building more on top |
| LLM hallucinates items or amounts | Strict schema, business-rule validation, catalog matching, merchant review for low-confidence lines |
| Two customers pay the same amount at once | Tier-2 penalty for multiple candidates → merchant confirmation instead of a wrong auto-match |
| Duplicate alert delivery | Idempotency keys + UNIQUE constraints |
| Diarization labels drift across sessions | Per-session calibration phrase; correctness never depends on labels alone |
| Realtime session limits | Proactive 55-minute rollover, silence pacing, backoff on rate-limit close codes |
| Privacy of counter conversations | Audio never stored; only structured transactions persisted; documented in README |

---

## 12. Resume mapping (fill in only with real results)

> **Kirana Kare — Voice-First Bookkeeping & Payment Reconciliation Agent** github.com/abhib05/Kirana-Kare
> ● Built a voice agent that listens at a kirana counter and automatically logs every **cash and UPI sale** and **customer credit (udhaar)** into an append-only **PostgreSQL ledger**, streaming audio from a **React web app** over **WebSocket** to **Meta's Muse Voice Transcribe** (code-switched Hindi/Telugu, speaker diarization) and an **LLM** that extracts items, quantities, and amounts.
> ● Built an idempotent **payment reconciliation engine** (Python FastAPI, Redis) that matches each UPI alert to its sale by reference ID, then amount and time window, then fuzzy payer name, with **confidence scoring** that routes uncertain matches to the merchant for one-tap approval; auto-matched **[X]%** of simulated UPI payments at **[Y]%** precision across **[N]** test transactions, with **[Z]** pytest tests.

Interview talking points this plan gives you: integer-paise money handling, append-only ledgers with reversals, idempotency and exactly-once matching, confidence-based human-in-the-loop decisions, validating LLM output, realtime WebSocket session management, and measuring precision honestly.
