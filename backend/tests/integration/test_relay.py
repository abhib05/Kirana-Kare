"""Relay tests against a real local WebSocket server that behaves like Muse realtime.

The "soak" test compresses the 55-minute rollover to a fraction of a second and checks
that every turn the server finalizes — including the one drained after endStream on the
old session — reaches the turn handler exactly once.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import pytest
from websockets.asyncio.server import ServerConnection, serve

from app.asr.relay import (
    BYTES_PER_SECOND,
    CALIBRATION_PHRASE,
    FRAME_BYTES,
    MAX_AHEAD_SECONDS,
    RelayConfig,
    RelaySession,
    UpstreamError,
    UpstreamSession,
)

TURN_EVERY_BYTES = FRAME_BYTES * 3
FRAME = b"\x01\x00" * (FRAME_BYTES // 2)


@dataclass
class FakeMuse:
    """Emits one diarized turn every TURN_EVERY_BYTES of audio. The first turn of each
    session is the calibration phrase from speaker B (the merchant)."""

    reject_auth: bool = False
    merge_calibration: bool = False  # real Muse often merges the phrase with the next words
    abort_session_after_bytes: dict[int, tuple[int, int]] = field(default_factory=dict)
    handshakes: list[dict] = field(default_factory=list)
    sessions: list[dict] = field(default_factory=list)
    finalized: list[str] = field(default_factory=list)

    async def handler(self, ws: ServerConnection) -> None:
        sid = parse_qs(urlparse(ws.request.path).query)["sessionId"][0]
        hs = json.loads(await ws.recv())
        self.handshakes.append(hs)
        if self.reject_auth:
            await ws.send(json.dumps({"type": "error", "message": "bad key", "sessionId": sid}))
            await ws.close(1008)
            return
        await ws.send(json.dumps({"sessionId": sid}))
        idx = len(self.sessions)
        state = {"sid": sid, "bytes": 0, "silence_frames": 0, "ended": False, "turns": 0}
        self.sessions.append(state)

        async def emit_turn(text: str, label: str) -> None:
            t = state["turns"]  # the real service numbers turns from 0
            state["turns"] += 1
            await ws.send(json.dumps({"type": "speechStart", "turnId": t}))
            await ws.send(json.dumps({"type": "speaker", "label": label}))
            await ws.send(json.dumps({"type": "transcript", "transcript": text[:5],
                                      "final": False}))
            await ws.send(json.dumps({"type": "speechEnd", "turnId": t}))
            await ws.send(json.dumps({"type": "speechComplete", "turnId": t,
                                      "transcript": text}))
            if text != CALIBRATION_PHRASE:
                self.finalized.append(f"{sid}:{t}")

        async for msg in ws:
            if isinstance(msg, bytes):
                if not any(msg):
                    state["silence_frames"] += 1
                state["bytes"] += len(msg)
                if idx in self.abort_session_after_bytes:
                    limit, code = self.abort_session_after_bytes[idx]
                    if state["bytes"] >= limit:
                        await ws.close(code)
                        return
                while state["bytes"] >= TURN_EVERY_BYTES * (state["turns"] + 1):
                    if state["turns"] == 0 and self.merge_calibration:
                        await emit_turn(f"{CALIBRATION_PHRASE} do kilo atta", "B")
                    elif state["turns"] == 0:
                        await emit_turn(CALIBRATION_PHRASE, "B")
                    else:
                        await emit_turn(f"ek parle g turn {state['turns']}", "A")
            elif json.loads(msg).get("type") == "endStream":
                state["ended"] = True
                await emit_turn("bas cash, last words before close", "B")  # in-flight turn
                await ws.close(1000)
                return


@pytest.fixture
async def muse():
    fake = FakeMuse()
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        fake.url = f"ws://127.0.0.1:{port}/v1/asr/realtime"  # type: ignore[attr-defined]
        yield fake


def cfg(url: str, **kw) -> RelayConfig:
    base = dict(url=url, api_key="sk-test", model="muse-voice-transcribe-1.0",
                language_bias=["Hindi", "English", "Telugu"], keywords=["Atta", "udhaar"],
                silence_gap_s=0.05, backoff_initial_s=0.01, backoff_rate_limited_s=0.05,
                drain_timeout_s=2.0)
    base.update(kw)
    return RelayConfig(**base)


class Recorder:
    def __init__(self) -> None:
        self.turns: list = []
        self.messages: list[dict] = []

    async def handle(self, turn) -> dict:
        self.turns.append(turn)
        return {"status": "applied"}

    async def notify(self, msg: dict) -> None:
        self.messages.append(msg)

    def statuses(self) -> list[str]:
        return [m["state"] for m in self.messages if m["type"] == "status"]


async def feed_for(relay: RelaySession, seconds: float, frame_interval: float = 0.01) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        await relay.feed(FRAME)
        await asyncio.sleep(frame_interval)


async def test_handshake_turns_and_merchant_calibration(muse):
    rec = Recorder()
    relay = RelaySession(cfg(muse.url), rec.handle, rec.notify)
    await relay.start()
    for _ in range(10):
        await relay.feed(FRAME)
    await asyncio.sleep(0.2)
    await relay.stop()

    hs = muse.handshakes[0]
    assert hs["authorization"] == {"accessToken": "Bearer sk-test"}
    assert (hs["mode"], hs["audioEncoding"]) == ("DIARIZATION", "PCM_24KHZ")
    assert "Atta" in hs["keywords"]
    assert relay.sessions[0].merchant_label == "B"
    texts = [t.text for t in rec.turns]
    assert CALIBRATION_PHRASE not in texts  # the calibration phrase is not a sale
    assert texts[:2] == ["ek parle g turn 1", "ek parle g turn 2"]
    assert rec.turns[0].speaker == "A" and rec.turns[0].is_merchant is False
    assert rec.turns[-1].speaker == "B" and rec.turns[-1].is_merchant is True  # drained turn
    assert any(m["type"] == "partial" for m in rec.messages)
    assert muse.sessions[0]["ended"] is True
    assert {"calibrating", "hearing", "listening", "offline"} <= set(rec.statuses())


async def test_sale_merged_into_calibration_turn_is_not_dropped(muse):
    muse.merge_calibration = True
    rec = Recorder()
    relay = RelaySession(cfg(muse.url), rec.handle, rec.notify)
    await relay.start()
    for _ in range(4):
        await relay.feed(FRAME)
    await asyncio.sleep(0.2)
    await relay.stop()
    assert relay.sessions[0].merchant_label == "B"
    first = rec.turns[0]
    assert first.text == "do kilo atta"
    assert first.turn_id.endswith(":0") and first.is_merchant is True


async def test_compressed_soak_rollover_loses_no_turns(muse):
    rec = Recorder()
    relay = RelaySession(cfg(muse.url, rollover_after_s=0.4), rec.handle, rec.notify)
    await relay.start()
    await feed_for(relay, 1.5)
    await relay.stop()

    assert len(muse.sessions) >= 3  # rolled over at least twice
    assert all(s["ended"] for s in muse.sessions)  # every old session got endStream
    handled = [t.turn_id for t in rec.turns]
    assert sorted(handled) == sorted(muse.finalized)  # nothing lost, nothing duplicated
    assert len(set(handled)) == len(handled)
    assert "rollover" in rec.statuses()
    # Each new session re-calibrated independently.
    assert all(s.merchant_label == "B" for s in relay.sessions)


async def test_unexpected_close_reconnects(muse):
    muse.abort_session_after_bytes = {0: (FRAME_BYTES * 4, 1011)}
    rec = Recorder()
    relay = RelaySession(cfg(muse.url), rec.handle, rec.notify)
    await relay.start()
    await feed_for(relay, 0.6)
    await relay.stop()
    assert len(muse.sessions) == 2
    assert "reconnecting" in rec.statuses()
    assert muse.sessions[1]["bytes"] > 0  # audio resumed on the new session


async def test_rate_limit_close_backs_off_longer(muse):
    muse.abort_session_after_bytes = {0: (FRAME_BYTES * 2, 1013)}
    rec = Recorder()
    relay = RelaySession(cfg(muse.url), rec.handle, rec.notify)
    await relay.start()
    await feed_for(relay, 0.5)
    await relay.stop()
    first = next(m for m in rec.messages if m.get("state") == "reconnecting")
    assert first["delay_s"] == 0.05  # backoff_rate_limited_s, not backoff_initial_s


async def test_handshake_rejection_raises(muse):
    muse.reject_auth = True
    rec = Recorder()
    relay = RelaySession(cfg(muse.url), rec.handle, rec.notify)
    with pytest.raises(UpstreamError, match="bad key"):
        await relay.start()


async def test_silence_keeps_stream_alive(muse):
    rec = Recorder()
    relay = RelaySession(cfg(muse.url), rec.handle, rec.notify)
    await relay.start()
    await asyncio.sleep(0.4)  # browser sends nothing
    await relay.stop()
    assert muse.sessions[0]["silence_frames"] >= 3


async def test_turn_handler_failure_does_not_kill_relay(muse):
    rec = Recorder()
    calls = 0

    async def flaky(turn):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db down")
        return {"status": "applied"}

    relay = RelaySession(cfg(muse.url), flaky, rec.notify)
    await relay.start()
    for _ in range(10):
        await relay.feed(FRAME)
    await asyncio.sleep(0.2)
    await relay.stop()
    results = [m["result"]["status"] for m in rec.messages if m["type"] == "turn"]
    assert results[0] == "error" and "applied" in results[1:]


class _FakeWS:
    close_code = None

    def __init__(self) -> None:
        self.sent = 0

    async def send(self, m):
        self.sent += len(m)


async def test_pacing_never_runs_more_than_five_seconds_ahead(monkeypatch):
    now = [0.0]
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)
        now[0] += s

    monkeypatch.setattr("app.asr.relay.asyncio.sleep", fake_sleep)
    s = UpstreamSession(cfg("ws://unused"), connector=None, on_event=None,  # type: ignore[arg-type]
                        clock=lambda: now[0])
    s.ws = _FakeWS()  # type: ignore[assignment]
    for _ in range(int(10 * BYTES_PER_SECOND / FRAME_BYTES)):  # 10 s of audio, instantly
        await s.send_audio(bytes(FRAME_BYTES))
        ahead = s.bytes_sent / BYTES_PER_SECOND - now[0]
        assert ahead <= MAX_AHEAD_SECONDS + FRAME_BYTES / BYTES_PER_SECOND
    assert sum(sleeps) == pytest.approx(10 - MAX_AHEAD_SECONDS, abs=0.1)
