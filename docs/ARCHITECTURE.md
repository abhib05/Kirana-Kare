# Architecture

```
 Browser (React + Tailwind)                         FastAPI backend (Python 3.12)
 ┌─────────────────────────────┐   WS /ws/audio    ┌──────────────────────────────────────────┐
 │ Listener: AudioWorklet mic  │ ────────────────▶ │ asr/relay.py ──wss──▶ Muse Voice Transcribe│
 │ → 24 kHz PCM16, 80 ms frames│ ◀──── status ──── │   handshake · pacing · silence · rollover │
 └─────────────────────────────┘   partial/turns   │   speechComplete ──▶ ordered turn worker   │
 ┌─────────────────────────────┐                   │                         │                  │
 │ Dashboard: Live · Confirm · │   WS /ws/events   │ extraction/ (LLM or rules) → Pydantic +    │
 │ Udhaar · Inventory ·        │ ◀──────────────── │   business rules → catalog_match           │
 │ Restock · Summary           │      + REST       │                         ▼                  │
 └─────────────────────────────┘ ────────────────▶ │ baskets/engine.py (Redis session, PG truth)│
                                                   │        │ cash/udhaar          │ UPI        │
   POST /api/alerts, /api/alerts/parse-sms ──────▶ │ alerts/ingest (idempotent) ──▶ reconciliation│
                                                   │        ▼                      (3 tiers)    │
                                                   │ baskets/closing.py ─▶ ledger/service.post  │
                                                   │                    └▶ inventory → restock  │
                                                   │ reminders/jobs.py (APScheduler) → wa.me    │
                                                   └──────────────────────────────────────────┘
                                                        PostgreSQL 16          Redis 7
```

## The path of one UPI sale
1. **Audio.** The AudioWorklet posts 128-sample Float32 blocks; `pcm.ts` resamples to 24 kHz,
   converts to Int16 LE and cuts 80 ms frames. Muted audio is sent as silence so the ASR
   stream keeps real-time pacing. Audio is only ever in memory.
2. **ASR.** `RelaySession` opens a Muse session (handshake → `{sessionId}` ack), forwards
   frames no more than 4 s ahead of real time, and injects silence when the browser is
   quiet. `speaker` labels attach to the latest `speechStart`; `speechComplete` is a final
   turn, queued in order for extraction. Sessions roll over at 55 min (new session first,
   then `endStream` + drain on the old one) and reconnect with backoff on unexpected close.
3. **Extraction.** The turn plus the last 6 turns and the catalog go to the LLM with a JSON
   schema. Output must parse into `ExtractedTurn` and pass business rules (positive and
   plausible quantities, spoken price within 0.5×–3× catalog); otherwise the whole turn is
   rejected and logged to `audit_log`. Items map to SKUs with RapidFuzz.
4. **Basket.** One open basket per conversation window. "GPay" moves it to
   `awaiting_payment`, assigns a `KK` + 6 base32 ref and a `upi://pay` link (shown as a QR).
5. **Alert.** A simulated/webhook alert or a pasted SMS is inserted with
   `ON CONFLICT (idempotency_key) DO NOTHING`; duplicates return the original row.
6. **Reconciliation.** Pure scoring (`scoring.py`) over candidate baskets: ref match 0.99;
   exact amount in ±10 min 0.75 with uniqueness/tie/time adjustments; payer-name evidence
   ±. Auto-match needs ≥ 0.80 and a unique best; everything else goes to the confirm queue
   with the top 3 and the reasons. The engine locks the alert row and the basket row, so a
   payment is applied exactly once even under concurrent deliveries.
7. **Close.** `close_basket` posts `sale_upi` through the ledger service, decrements stock
   and refreshes the vendor's restock draft, all in the same transaction.

## Data invariants
- Money is `BIGINT` paise everywhere; `rupees_to_paise` refuses floats.
- `ledger_entries` is append-only (trigger rejects UPDATE/DELETE); corrections are
  `reversal` rows; an entry can be reversed once; balances come from the `udhaar_balances`
  view. Hypothesis tests compare the view with an in-memory model over random sequences.
- `upi_alerts.idempotency_key` UNIQUE, `matches.alert_id` UNIQUE.
- Every reconciliation decision (inputs, scored candidates, outcome) and every merchant
  decision is written to `audit_log`.

## Where state lives
| State | Store | Why |
|---|---|---|
| Baskets, items, alerts, matches, ledger, drafts, reminders | PostgreSQL | durable, transactional |
| Open-basket index, basket cache, last 6 turns, per-shop lock | Redis | hot session state |
| Live events to dashboards | in-process bus → `/ws/events` | single backend process in the MVP |

The event bus is in-process; running several backend replicas would need Redis pub/sub
in its place (noted in README "Future work").
