# Kirana Copilot — the ledger that listens

An "invisible ERP" for kiranas (HackSprint PS21). The counter hears each sale in Hindi / Kannada / Telugu,
pairs it with the incoming UPI alert, and updates sales, udhaar (credit) and stock automatically.

## What's built

| Deck feature | Where |
|---|---|
| Always-on listener, speech → basket | `frontend` Counter panel (browser mic / typed) and `POST /api/audio` (Sarvam STT) |
| Transaction filter (chit-chat dropped) + item extraction | `backend/app/extractor.py` (rules offline; LLM when `ANTHROPIC_API_KEY` set) |
| UPI alert parsing (PhonePe/GPay/Paytm/SMS) | `backend/app/alerts.py`, `android-companion/UpiAlertListener.kt` |
| Reconciliation: ref-ID → amount+time → fuzzy payer name, confidence score, confirm if < 80% | `matcher.py`, `engine.py` |
| Spoken udhaar ("khate mein likh do") + ledger | `engine.py` (`ledger` table) |
| Tagged UPI pay links clearing exact debt | `POST /api/paylink` → `upi://pay?...&tn=KC-C<id>` |
| Auto recovery reminders in customer's language | `POST /api/reminders/run` (hi/kn/te templates) |
| Predictive restock | `Engine.forecast` (14-day sales velocity → days left, suggested order) |
| Privacy: audio never stored | audio held in memory only, discarded after transcription; only transactions saved |
| Live dashboard | React + Tailwind, pushed over WebSocket `/ws/events` |

Honest scope notes: storage is SQLite (the schema ports directly to Postgres/Supabase); on-device Silero VAD and
speaker-ID (merchant voiceprint) are not implemented; the Kotlin companion is source only (not built here);
WhatsApp messages go to the in-app outbox unless WhatsApp keys are configured.

## Run it

```bash
# backend (Python 3.11+)
cd backend && pip install -r requirements.txt
cp .env.example .env   # optional keys; works offline without them
python -m uvicorn app.main:app --port 8000      # seeds demo data on first start
python -m pytest -q                              # 18 tests

# frontend
cd frontend && npm install
npm run dev        # http://localhost:5173 (proxies to :8000)
npm run build      # outputs to backend/static, then http://localhost:8000 serves everything
```

## 2-minute demo script
1. Counter panel → click *ek kilo cheeni dena aur do packet maggi* → basket ₹74 appears, "Waiting for UPI".
2. UPI simulator → ₹74 → feed shows **Auto-matched** (~85%), stock drops.
3. Say a basket, wait or fire an ambiguous amount → **Needs your tap** → ✓/✗.
4. *ek kilo atta Ramesh ke khate mein likh do* → udhaar rises. Click **Pay link**, then fire a UPI alert with note `KC-C1` → debt clears.
5. **Send reminders** → Hindi/Kannada/Telugu messages in the outbox. Low-stock card shows predicted stock-outs.
6. "aaj bahut garmi hai" → discarded.

## Deploy
`backend/Dockerfile` (after `npm run build`, static dashboard is served by FastAPI) → Google Cloud Run.
