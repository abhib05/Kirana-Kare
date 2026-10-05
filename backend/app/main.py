import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import integrations
from .config import get_settings
from .db import Database
from .engine import Engine
from .seed import seed

db = Database(get_settings().db_path)
engine = Engine(db)
clients: set[WebSocket] = set()


async def publish():
    for ev in engine.drain_events():
        for ws in list(clients):
            try:
                await ws.send_json(ev)
            except Exception:
                clients.discard(ws)


@asynccontextmanager
async def lifespan(_):
    seed(db)
    yield


app = FastAPI(title="Kirana Copilot", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Utterance(BaseModel):
    text: str


class AlertIn(BaseModel):
    raw: str | None = None
    amount: float | None = None
    payer: str | None = None
    upi_ref: str | None = None
    tag_ref: str | None = None
    app: str = "UPI"


class Assign(BaseModel):
    customer_id: int


class PayLinkIn(BaseModel):
    customer_id: int
    amount: float | None = None


class RemindIn(BaseModel):
    min_age_days: float = 3


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "stt": "sarvam" if s.sarvam_key else "browser-mic/text",
            "extractor": "llm+rules" if s.anthropic_key else "rules",
            "whatsapp": "live" if s.wa_token else "outbox-only"}


@app.post("/api/utterance")
async def utterance(u: Utterance):
    out = engine.handle_utterance(u.text)
    await publish()
    return out


@app.post("/api/audio")
async def audio(file: UploadFile = File(...), language: str = Form("unknown")):
    """Counter-device audio chunk (speech-only, VAD-gated). Held in memory, transcribed, discarded."""
    data = await file.read()
    try:
        text = await asyncio.to_thread(integrations.transcribe, data, file.filename or "a.wav",
                                       file.content_type or "audio/wav", language)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    finally:
        del data
    out = engine.handle_utterance(text) if text else {"discarded": True, "reason": "empty transcript"}
    await publish()
    return {"transcript": text, **out}


@app.post("/api/upi-alert")
async def upi_alert(a: AlertIn):
    """Fed by the Android companion's NotificationListener or a PSP webhook."""
    fields = None
    if a.raw is None:
        if a.amount is None:
            raise HTTPException(422, "send either raw text or amount")
        fields = {"amount": a.amount, "payer": a.payer, "upi_ref": a.upi_ref, "tag_ref": a.tag_ref, "app": a.app}
    out = engine.handle_alert(raw=a.raw, fields=fields)
    await publish()
    return out


@app.post("/api/alerts/{aid}/confirm")
async def confirm(aid: int):
    try:
        out = engine.confirm_alert(aid)
    except ValueError as e:
        raise HTTPException(409, str(e))
    await publish()
    return out


@app.post("/api/alerts/{aid}/reject")
async def reject(aid: int):
    try:
        out = engine.reject_alert(aid)
    except ValueError as e:
        raise HTTPException(409, str(e))
    await publish()
    return out


@app.post("/api/baskets/{bid}/assign")
async def assign(bid: int, a: Assign):
    try:
        out = engine.assign_customer(bid, a.customer_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    await publish()
    return out


@app.post("/api/paylink")
def paylink(p: PayLinkIn):
    try:
        return engine.paylink(p.customer_id, p.amount)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/reminders/run")
async def reminders(r: RemindIn):
    sent = engine.run_reminders(min_age_days=r.min_age_days)
    await publish()
    return {"sent": sent}


@app.get("/api/summary")
def summary():
    return engine.summary()


@app.get("/api/feed")
def feed():
    return engine.feed()


@app.get("/api/customers")
def customers():
    return engine.customers()


@app.get("/api/inventory")
def inventory():
    return engine.forecast()


@app.get("/api/outbox")
def outbox():
    return db.q("SELECT * FROM outbox ORDER BY ts DESC LIMIT 30")


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)


# Serve the built dashboard when present (single-container deploy on Cloud Run).
_dist = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_dist, "assets")), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(os.path.join(_dist, "index.html"))
