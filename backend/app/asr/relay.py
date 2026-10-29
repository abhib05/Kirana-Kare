"""Browser WS <-> Muse Voice Transcribe realtime relay (§5.2).

One `RelaySession` per listening browser. It owns one active `UpstreamSession` at a time:

- Handshake is the first text frame; audio is forwarded only after the `{sessionId}` ack.
- Pacing: never more than ~4 s ahead of real time (the server allows 5); when the browser
  goes quiet the relay injects 80 ms PCM silence frames so the stream never idles out.
- `speaker` events carry no turnId, so a label applies to the latest `speechStart`.
- `speechComplete` is the final text of a turn; turns are handed, in order, to a worker
  that runs extraction off the event loop. Turn ids are namespaced by upstream session id.
- Rollover: sessions are capped at 60 min and cannot be resumed, so at ~55 min a new
  session is opened and audio switches to it; the old one gets `endStream` and is drained
  until it closes, so turns in flight on it are not lost.
- Unexpected closes reconnect with exponential backoff (longer for 1013 rate limits).
- Merchant calibration: the first turn on each session that sounds like the calibration
  phrase marks that speaker label as the merchant for that session only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from rapidfuzz import fuzz, utils

from app.asr.events import (
    ErrorEvent,
    HandshakeAck,
    MuseEvent,
    SpeakerEvent,
    SpeechComplete,
    SpeechEnd,
    SpeechStart,
    TranscriptEvent,
    handshake,
    parse_event,
)
from app.extraction.schemas import Turn

log = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # PCM16 mono
FRAME_BYTES = 3_840  # 80 ms
MAX_AHEAD_SECONDS = 4.0
CALIBRATION_PHRASE = "kirana kare shuru karo"
CALIBRATION_MIN_SCORE = 75.0
RATE_LIMITED = 1013


class UpstreamError(RuntimeError):
    pass


class WS(Protocol):
    """The slice of a websockets connection the relay uses (lets tests fake it)."""

    close_code: int | None

    async def send(self, message: str | bytes) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...
    def __aiter__(self) -> Any: ...


Connector = Callable[[str], Awaitable[WS]]
TurnHandler = Callable[[Turn], Awaitable[dict[str, Any]]]
Notifier = Callable[[dict[str, Any]], Awaitable[None]]


async def default_connector(url: str) -> WS:
    from websockets.asyncio.client import connect

    return await connect(url, max_size=2**20, open_timeout=10)  # type: ignore[return-value]


@dataclass
class RelayConfig:
    url: str
    api_key: str
    model: str
    language_bias: list[str]
    keywords: list[str]
    rollover_after_s: float = 55 * 60
    handshake_timeout_s: float = 10.0
    silence_gap_s: float = 0.25
    drain_timeout_s: float = 30.0
    backoff_initial_s: float = 1.0
    backoff_rate_limited_s: float = 5.0
    backoff_max_s: float = 30.0
    max_reconnect_attempts: int = 8


def is_calibration_phrase(text: str) -> bool:
    return fuzz.partial_ratio(utils.default_process(text), CALIBRATION_PHRASE) >= \
        CALIBRATION_MIN_SCORE


@dataclass
class UpstreamSession:
    cfg: RelayConfig
    connector: Connector
    on_event: Callable[[UpstreamSession, MuseEvent], Awaitable[None]]
    clock: Callable[[], float] = time.monotonic
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ws: WS | None = None
    started_at: float = 0.0
    bytes_sent: int = 0
    current_turn: int | None = None
    speakers: dict[int, str] = field(default_factory=dict)
    merchant_label: str | None = None
    ending: bool = False
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    close_code: int | None = None
    _reader: asyncio.Task[None] | None = None

    async def open(self) -> None:
        self.ws = await self.connector(f"{self.cfg.url}?sessionId={self.session_id}")
        await self.ws.send(json.dumps(handshake(
            self.cfg.api_key, self.cfg.model, self.cfg.language_bias, self.cfg.keywords)))
        raw = await asyncio.wait_for(self.ws.recv(), self.cfg.handshake_timeout_s)
        ack = parse_event(json.loads(raw))
        if isinstance(ack, ErrorEvent):
            await self.ws.close()
            raise UpstreamError(f"handshake rejected: {ack.message}")
        if not isinstance(ack, HandshakeAck):
            await self.ws.close()
            raise UpstreamError(f"unexpected handshake reply: {raw!r:.200}")
        self.session_id = ack.session_id
        self.started_at = self.clock()
        self._reader = asyncio.create_task(self._read(), name=f"muse-read-{self.session_id}")

    @property
    def age(self) -> float:
        return self.clock() - self.started_at

    async def send_audio(self, frame: bytes) -> None:
        assert self.ws is not None
        ahead = self.bytes_sent / BYTES_PER_SECOND - self.age
        if ahead > MAX_AHEAD_SECONDS:
            await asyncio.sleep(ahead - MAX_AHEAD_SECONDS)
        await self.ws.send(frame)
        self.bytes_sent += len(frame)

    async def _read(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    event = parse_event(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    log.warning("muse %s: non-JSON frame ignored", self.session_id)
                    continue
                await self.on_event(self, event)
        except Exception as exc:  # connection dropped: the watcher decides what to do
            log.info("muse %s reader ended: %s", self.session_id, type(exc).__name__)
        finally:
            self.close_code = getattr(self.ws, "close_code", None)
            self.closed.set()

    async def end(self) -> None:
        """endStream, then keep reading until the server closes (1000)."""
        self.ending = True
        if self.ws is None:
            return
        with contextlib.suppress(Exception):
            await self.ws.send(json.dumps({"type": "endStream"}))
        try:
            await asyncio.wait_for(self.closed.wait(), self.cfg.drain_timeout_s)
        except TimeoutError:
            log.warning("muse %s did not close after endStream; closing", self.session_id)
            with contextlib.suppress(Exception):
                await self.ws.close()


class RelaySession:
    def __init__(self, cfg: RelayConfig, turn_handler: TurnHandler, notify: Notifier,
                 connector: Connector = default_connector,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.turn_handler = turn_handler
        self.notify = notify
        self.connector = connector
        self.clock = clock
        self.active: UpstreamSession | None = None
        self.sessions: list[UpstreamSession] = []
        self.stopping = False
        self.last_frame_at = clock()
        self._turns: asyncio.Queue[Turn] = asyncio.Queue()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._switch_lock = asyncio.Lock()
        self._reconnecting = False

    def _spawn(self, coro: Awaitable[Any], name: str) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        task.set_name(name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _status(self, state: str, **extra: Any) -> None:
        await self.notify({"type": "status", "state": state, **extra})

    # ---- lifecycle -----------------------------------------------------------------------
    async def start(self) -> None:
        self._spawn(self._turn_worker(), "turn-worker")
        self.active = await self._open_new()
        self._spawn(self._keepalive(), "silence-keepalive")

    async def _open_new(self) -> UpstreamSession:
        s = UpstreamSession(self.cfg, self.connector, self._on_event, self.clock)
        await s.open()
        self.sessions.append(s)
        self._spawn(self._watch(s), f"watch-{s.session_id}")
        log.info("muse session %s open", s.session_id)
        await self._status("calibrating", session_id=s.session_id,
                           prompt=f'Say "{CALIBRATION_PHRASE}"')
        return s

    async def stop(self) -> None:
        self.stopping = True
        if self.active is not None:
            await self.active.end()
        for s in self.sessions:
            if not s.closed.is_set():
                await s.end()
        await self._turns.join()  # finish extracting every turn already received
        for t in list(self._tasks):
            t.cancel()
        await self._status("offline")

    # ---- audio path ----------------------------------------------------------------------
    async def feed(self, frame: bytes) -> None:
        self.last_frame_at = self.clock()
        await self._send(frame)

    async def _send(self, frame: bytes) -> None:
        async with self._switch_lock:
            s = self.active
            if s is None or self.stopping:
                return  # reconnecting: audio during the gap is dropped, not queued
            if s.age >= self.cfg.rollover_after_s:
                s = await self._rollover(s)
        try:
            await s.send_audio(frame)
        except Exception as exc:
            log.info("send to muse %s failed: %s", s.session_id, type(exc).__name__)

    async def _rollover(self, old: UpstreamSession) -> UpstreamSession:
        try:
            new = await self._open_new()
        except Exception:
            log.exception("rollover: could not open a new session; keeping the old one")
            old.started_at = self.clock() - self.cfg.rollover_after_s + 30  # retry in 30 s
            return old
        self.active = new
        self._spawn(old.end(), f"drain-{old.session_id}")
        await self._status("rollover", old_session_id=old.session_id,
                           session_id=new.session_id)
        return new

    async def _keepalive(self) -> None:
        silence = bytes(FRAME_BYTES)
        while not self.stopping:
            await asyncio.sleep(self.cfg.silence_gap_s / 2)
            if self.clock() - self.last_frame_at >= self.cfg.silence_gap_s:
                await self._send(silence)

    # ---- connection health -----------------------------------------------------------------
    async def _watch(self, s: UpstreamSession) -> None:
        await s.closed.wait()
        if s is self.active and not s.ending and not self.stopping:
            log.warning("muse %s closed unexpectedly (code %s)", s.session_id, s.close_code)
            await self._reconnect(s.close_code)

    async def _reconnect(self, code: int | None) -> None:
        if self._reconnecting:
            return
        self._reconnecting = True
        self.active = None
        delay = (self.cfg.backoff_rate_limited_s if code == RATE_LIMITED
                 else self.cfg.backoff_initial_s)
        try:
            for attempt in range(1, self.cfg.max_reconnect_attempts + 1):
                await self._status("reconnecting", attempt=attempt, delay_s=delay)
                await asyncio.sleep(delay)
                if self.stopping:
                    return
                try:
                    self.active = await self._open_new()
                    return
                except Exception as exc:
                    log.warning("reconnect attempt %d failed: %s", attempt, exc)
                    delay = min(delay * 2, self.cfg.backoff_max_s)
            await self._status("offline", reason="could not reconnect to speech service")
        finally:
            self._reconnecting = False

    # ---- events --------------------------------------------------------------------------
    async def _on_event(self, s: UpstreamSession, ev: MuseEvent) -> None:
        if isinstance(ev, SpeechStart):
            s.current_turn = ev.turn_id
            await self._status("hearing")
        elif isinstance(ev, TranscriptEvent):
            await self.notify({"type": "partial", "text": ev.transcript,
                               "session_id": s.session_id, "turn": s.current_turn})
        elif isinstance(ev, SpeakerEvent):
            if s.current_turn is not None:
                s.speakers[s.current_turn] = ev.label
        elif isinstance(ev, SpeechEnd):
            await self._status("listening")
        elif isinstance(ev, SpeechComplete):
            await self._complete(s, ev)
        elif isinstance(ev, ErrorEvent):
            log.error("muse error (session %s): %s", ev.session_id or s.session_id, ev.message)
            await self.notify({"type": "error", "message": ev.message,
                               "session_id": ev.session_id or s.session_id})

    async def _complete(self, s: UpstreamSession, ev: SpeechComplete) -> None:
        label = s.speakers.pop(ev.turn_id, None)
        text = ev.transcript.strip()
        if not text:
            return
        if s.merchant_label is None and label is not None and is_calibration_phrase(text):
            s.merchant_label = label
            await self._status("listening", calibrated=True, merchant_label=label,
                               session_id=s.session_id)
            return
        await self._turns.put(Turn(
            turn_id=f"{s.session_id}:{ev.turn_id}", text=text, ts=datetime.now(UTC),
            speaker=label,
            is_merchant=(label == s.merchant_label) if s.merchant_label and label else None,
        ))

    async def _turn_worker(self) -> None:
        while True:
            turn = await self._turns.get()
            try:
                result = await self.turn_handler(turn)
                await self.notify({"type": "turn", "turn_id": turn.turn_id, "text": turn.text,
                                   "speaker": turn.speaker, "is_merchant": turn.is_merchant,
                                   "result": result})
            except Exception:
                log.exception("turn %s failed", turn.turn_id)
                await self.notify({"type": "turn", "turn_id": turn.turn_id, "text": turn.text,
                                   "result": {"status": "error"}})
            finally:
                self._turns.task_done()
