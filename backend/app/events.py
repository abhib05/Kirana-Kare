"""In-process event bus feeding `WS /ws/events`.

Services (sync code running in FastAPI's threadpool) call `publish()`; each connected
dashboard socket owns an asyncio.Queue. `publish` is thread-safe and never blocks: if
nobody is listening (tests, scripts) events are simply dropped.

Event types: partial, turn, basket_updated, basket_closed, alert_received, match_decided,
restock_draft, reminder, listener_status, extraction_rejected.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder


class EventBus:
    def __init__(self) -> None:
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subs.add((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subs = {s for s in self._subs if s[1] is not q}

    def publish(self, event_type: str, payload: Any) -> None:
        event = {
            "type": event_type,
            "ts": datetime.now(UTC).isoformat(),
            "data": jsonable_encoder(payload),
        }
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_put_nowait, q, event)
            except RuntimeError:  # loop closed
                self.unsubscribe(q)


def _put_nowait(q: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
    if q.full():  # slow consumer: drop the oldest event rather than block producers
        q.get_nowait()
    q.put_nowait(event)


bus = EventBus()
