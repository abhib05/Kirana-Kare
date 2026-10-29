"""WebSocket endpoints: /ws/audio (browser mic -> Muse relay) and /ws/events (dashboard)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from app.asr.relay import RelayConfig, RelaySession, UpstreamError, default_connector
from app.baskets.engine import BasketEngine, load_catalog
from app.config import get_settings
from app.db.models import Shop
from app.db.session import get_session_factory
from app.events import bus
from app.extraction.extractor import get_extractor
from app.extraction.schemas import Turn
from app.redis_client import get_redis

log = logging.getLogger(__name__)
router = APIRouter()

MAX_KEYWORDS = 50
FIXED_KEYWORDS = ["udhaar", "khata", "UPI", "GPay", "PhonePe", "Paytm"]

# Swappable in tests (fake Muse server / in-process handler).
connector = default_connector


def relay_config() -> RelayConfig:
    s = get_settings()
    with get_session_factory()() as db:
        catalog = load_catalog(db, s.demo_shop_id)
        shop = db.execute(select(Shop).where(Shop.id == s.demo_shop_id)).scalar_one_or_none()
    names = [p.name for p in catalog][: MAX_KEYWORDS - len(FIXED_KEYWORDS)]
    return RelayConfig(
        url=s.asr_url, api_key=s.model_api_key, model=s.asr_model,
        language_bias=list(shop.language_bias) if shop and shop.language_bias
        else s.language_bias,
        keywords=names + FIXED_KEYWORDS,
    )


async def process_turn(turn: Turn) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        s = get_settings()
        with get_session_factory()() as db:
            engine = BasketEngine(db, get_redis(), s.demo_shop_id, get_extractor(s))
            return engine.process_turn(turn).as_dict()

    return await run_in_threadpool(work)


@router.websocket("/ws/audio")
async def ws_audio(ws: WebSocket) -> None:
    """Binary frames: PCM16 LE mono @ 24 kHz. Text frames: {"type": "stop"}.
    Raw audio is only ever held in memory long enough to forward it; nothing is stored."""
    await ws.accept()
    send_lock = asyncio.Lock()

    async def notify(msg: dict[str, Any]) -> None:
        if msg["type"] in ("partial", "status"):
            bus.publish("partial" if msg["type"] == "partial" else "listener_status", msg)
        async with send_lock:
            with contextlib.suppress(Exception):
                await ws.send_json(msg)

    if not get_settings().model_api_key:
        await notify({"type": "status", "state": "offline",
                      "reason": "MODEL_API_KEY is not set on the backend; use typed turns"})
        await ws.close(code=1011)
        return

    relay = RelaySession(await run_in_threadpool(relay_config), process_turn, notify,
                         connector=connector)
    try:
        await relay.start()
    except (UpstreamError, OSError, TimeoutError) as exc:
        log.error("could not start ASR session: %s", exc)
        await notify({"type": "status", "state": "offline", "reason": str(exc)})
        await ws.close(code=1011)
        return

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                await relay.feed(msg["bytes"])
            elif msg.get("text"):
                with contextlib.suppress(json.JSONDecodeError):
                    if json.loads(msg["text"]).get("type") == "stop":
                        break
    except WebSocketDisconnect:
        pass
    finally:
        await relay.stop()
        with contextlib.suppress(Exception):
            await ws.close()


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
    queue = bus.subscribe()
    try:
        while True:
            event = await queue.get()
            await ws.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(queue)
