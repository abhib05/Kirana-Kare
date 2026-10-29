# Kirana Kare — the ledger that listens

A hardware-free, voice-first bookkeeping and UPI-reconciliation agent for Indian kirana
(neighbourhood grocery) shops.

A UPI soundbox says "₹150 received" but never *what* it was for. Credit (*udhaar*) lives in a
paper *khata*. Cash, UPI and credit sales are never recorded in one place. Kirana Kare listens
to the counter conversation in the browser, turns code-mixed Hindi / Telugu / English speech
into baskets, matches each UPI payment to the right basket, and keeps an append-only ledger
from which udhaar balances, stock, restock orders and reminders are derived.

```
"do kilo atta aur ek doodh" → basket ₹120 → "GPay karta hoon" → QR with ref KK…  →
UPI alert ₹120 → matched (tier 1, 0.99) → ledger sale_upi ₹120 → atta stock 40 → 38
```

## Evaluation

<!-- EVAL:START -->
Reconciliation, measured by replaying 40 synthetic shop-days through the production engine on
PostgreSQL. Thresholds were tuned on 20 dev days and scored on 20 held-out days, with the plan
defaults (auto-match ≥ 0.80, ±10 min) kept. Full report: [eval/REPORT.md](eval/REPORT.md).

| Held-out (667 UPI alerts) | Value |
|---|---|
| Auto-matched, all alerts | **80.7%** |
| Auto-matched, alerts for a captured sale | 90.5% (99.6% when the ref is echoed, 83.9% without) |
| Auto-match precision | **99.44%** (3 wrong auto-matches) |
| Sent to the merchant's confirm queue | 19.3%, with the right basket in the top 3 for 94.6% |
| Duplicate deliveries ignored | 100% (51 of 51); no alert ever posted twice |

The scenarios include rush hours, refs echoed only 40% of the time, same-amount collisions,
early payments, stray credits and 7% of sales the listener missed (whose payments have no basket
to match). Those missed sales account for most of the wrong auto-matches. A dev-tuned looser
threshold (0.70) looked fine on dev but dropped to 98.9% precision on held-out, so the defaults
stay.

Extraction, on 40 hand-labelled code-mixed turns (typed transcripts, so this measures extraction
without ASR):

| | LLM (`muse-spark-1.3`) | Rule-based baseline |
|---|---|---|
| Item precision / recall | **100% / 100%** | 97.7% / 95.5% |
| Quantity correct (found items) | 100% | 97.6% |
| Payment mode | 97.5% | 100% |
| Customer | 100% | 100% |
| Chit-chat vs sale | 97.5% | 95.0% |

The LLM's first run scored 0% on customers and turned "₹500" into ₹50,000. Both were fixed
before the numbers above: the schema now requires every field, and any amount that matches no
number actually spoken is rejected. Reports: [LLM](eval/EXTRACTION_REPORT_llm.md),
[rules](eval/EXTRACTION_REPORT_rules.md). The speech-to-items number needs recorded clips.
<!-- EVAL:END -->

## Quick start (about 5 minutes)

Requirements: Docker Desktop (Compose v2.24+). Optional: a Meta Model API key for live voice
and LLM extraction. Without one, the app uses the rule-based extractor and typed turns.

```bash
git clone https://github.com/abhib05/Kirana-Kare.git
```
```bash
cd Kirana-Kare && cp .env.example .env
```
```bash
docker compose up --build
```

Open <http://localhost:5173>. The backend (API docs at <http://localhost:8000/docs>) migrates
and seeds a demo shop on start: 40 products, 3 vendors, 10 customers.

### Demo script
1. **Listener** → type `do kilo atta aur ek packet doodh` → an open basket appears (₹120).
   With `MODEL_API_KEY` set, press *Start listening*, say "Kirana Kare shuru karo" once to
   mark your voice as the merchant, then speak instead of typing.
2. Type `bas, gpay karta hoon` → **Live** shows the basket awaiting payment with a UPI QR.
3. Press *Simulate payment (with ref)* → it auto-matches (tier 1) and moves to Recent sales.
4. Make two ₹14 baskets (`ek maggi`, `gpay`, `ek maggi`, `phonepe`) and press *Simulate (no ref)*
   on one → the alert lands in **Confirm** with both candidates at 50% and the reasons; one tap
   resolves it.
5. Type `aadha kilo cheeni`, then `Ramesh bhaiya ke khate mein likh do` → **Udhaar** shows the
   balance; *Remind* drafts a WhatsApp message with a UPI pay link whose ref auto-matches when paid.
6. Type `31 kilo atta`, `bas cash` → **Restock** shows a draft for the grains vendor with a
   one-tap *Open in WhatsApp* link.
7. Paste a bank SMS on **Live** (e.g. `Rs.150.00 credited to a/c XX1234 on 02-11-26 by a/c linked
   to VPA ramesh.k@okicici (UPI Ref No 430612345678).`). Pasting it twice is a no-op.
8. **Summary** shows today's cash / UPI / udhaar split and auto vs merchant-confirmed alerts.

## Architecture

```
Browser mic ─AudioWorklet→ 24 kHz PCM16 ─WS→ FastAPI relay ─wss→ Muse Voice Transcribe
                                                │ speechComplete turns
                                                ▼
                       LLM extractor → Pydantic + business rules → RapidFuzz catalog match
                                                ▼
UPI alert / pasted SMS → idempotent ingest → 3-tier reconciliation ← basket engine (Redis + PG)
                                                ▼
                 append-only PostgreSQL ledger → udhaar · stock → restock drafts · reminders
                                                ▼
                           React dashboard over WS /ws/events
```

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · decisions: [docs/DECISIONS.md](docs/DECISIONS.md)
· build log: [docs/CHANGELOG.md](docs/CHANGELOG.md) · original spec: [KIRANA_KARE_PLAN.md](KIRANA_KARE_PLAN.md).

| Layer | Choice |
|---|---|
| Frontend | React 18 + TypeScript (Vite), Tailwind CSS 4, AudioWorklet |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, websockets |
| Speech | Meta Muse Voice Transcribe realtime (diarization, language bias, keywords) |
| Extraction | Meta Model API (OpenAI-compatible, JSON-schema output) or rule-based fallback |
| Matching | RapidFuzz |
| Data | PostgreSQL 16 (ledger, views, trigger), Redis 7 (basket sessions) |
| Jobs | APScheduler (udhaar reminders, idle-basket sweep) |
| Quality | pytest + hypothesis, Vitest, ruff, mypy, GitHub Actions |

## Design choices worth asking about
- **Money is integer paise.** `rupees_to_paise` rejects floats; every column is `BIGINT`.
- **Append-only ledger.** A Postgres trigger rejects `UPDATE`/`DELETE`; corrections are
  reversal entries (each entry reversible once); balances come from a view. Property-based
  tests check the view against an in-memory model for random sequences of sales, payments and
  reversals.
- **Idempotency and exactly-once.** Alerts insert with `ON CONFLICT DO NOTHING` on their
  idempotency key; reconciliation locks the alert row and the basket row and `matches.alert_id`
  is unique. Tests fire 10 concurrent alerts at one basket and 8 workers at one alert: one
  match, one ledger entry, every time.
- **Confidence-based human in the loop.** Auto-match only when the score is ≥ 0.80 *and*
  clearly ahead of the runner-up; otherwise the merchant gets the top 3 with reasons.
- **LLM output is untrusted.** Strict schema (`extra="forbid"`), business rules, whole-turn
  rejection on any bad line, audit log of every rejection.
- **Realtime session management.** Handshake-before-audio, ≤ 4 s ahead of real time, silence
  keepalive, 55-minute rollover with drain, backoff that distinguishes rate limits (1013).

## Tests

```bash
docker compose up -d db
```
```bash
cd backend && uv sync && uv run pytest --cov=app
```
```bash
cd frontend && npm ci && npx vitest run
```

The backend suite has 234 tests: unit, Postgres integration, contract tests for every documented
Muse event, and relay tests against a local fake Muse server (including a compressed
rollover soak). Coverage is 94% overall and 98% on `reconciliation/`, `ledger/`, `alerts/` and
`extraction/schemas.py`. CI runs ruff, mypy, pytest with a 90% coverage gate on that core, the
Vitest suite, and the frontend build.

## Privacy
Audio is streamed for transcription and never stored, on the client or the server. Only
structured transactions are persisted. API keys live in the backend's `.env`; the browser
never talks to Meta.

## Limitations
- The real Muse service has been checked for handshake, streaming and clean close (code 1000),
  but the hour-plus soak with session rollover has only run against a local fake of the protocol.
- Reconciliation numbers come from synthetic shop-days, not real payment-app traffic.
- Extraction numbers above are on typed transcripts; the end-to-end (speech → items) number
  needs recorded clips (`eval/transcribe_clips.py`).
- Diarization labels are session-scoped; correctness never depends on them.
- Single demo merchant; the schema carries `shop_id` everywhere but there is no auth.
- The live event bus is in-process (one backend replica).

## Future work
Android notification-listener for PhonePe/GPay/Paytm alerts · real PSP webhooks · Raspberry Pi
counter listener · GST reports · demand forecasting · lending on verified cash flow ·
multi-tenant auth · Redis pub/sub for multiple backend replicas.
