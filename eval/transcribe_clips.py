"""Transcribe recorded counter clips through Muse Voice Transcribe, for the end-to-end
(ASR + extraction) eval.

Put mono 16-bit WAV clips in eval/recordings/ (gitignored) and a labels file
eval/recordings/labels.jsonl with one line per clip, same fields as
backend/tests/fixtures/golden_turns.jsonl plus "clip": "001.wav". Then:

  python eval/transcribe_clips.py            # writes eval/recordings/transcripts.jsonl
  python eval/run_extraction_eval.py --backend llm \
      --cases eval/recordings/labels.jsonl --transcripts eval/recordings/transcripts.jsonl

Clips are streamed through the same UpstreamSession the live relay uses (handshake,
pacing, endStream + drain); nothing is stored except the resulting text.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import wave
from array import array
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent / "backend"))
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app.asr.events import MuseEvent, SpeechComplete  # noqa: E402
from app.asr.relay import (  # noqa: E402
    FRAME_BYTES,
    SAMPLE_RATE,
    RelayConfig,
    UpstreamSession,
    default_connector,
)
from app.config import get_settings  # noqa: E402

RECORDINGS = EVAL_DIR / "recordings"


def load_pcm24k(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: need mono 16-bit PCM WAV")
        rate = w.getframerate()
        samples = array("h", w.readframes(w.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    if rate == SAMPLE_RATE:
        return samples.tobytes()
    step = rate / SAMPLE_RATE  # linear-interpolation resample, same idea as the browser's
    out = array("h")
    pos = 0.0
    while pos < len(samples) - 1:
        i = int(pos)
        frac = pos - i
        out.append(int(samples[i] * (1 - frac) + samples[i + 1] * frac))
        pos += step
    return out.tobytes()


async def transcribe(path: Path, cfg: RelayConfig) -> str:
    texts: list[str] = []

    async def on_event(_: UpstreamSession, ev: MuseEvent) -> None:
        if isinstance(ev, SpeechComplete) and ev.transcript.strip():
            texts.append(ev.transcript.strip())

    s = UpstreamSession(cfg, default_connector, on_event)
    await s.open()
    pcm = load_pcm24k(path) + bytes(SAMPLE_RATE)  # + 0.5 s silence so the last turn ends
    for i in range(0, len(pcm), FRAME_BYTES):
        await s.send_audio(pcm[i:i + FRAME_BYTES])
    await s.end()
    return " ".join(texts)


async def main() -> None:
    s = get_settings()
    if not s.model_api_key:
        sys.exit("MODEL_API_KEY is not set")
    labels = [json.loads(line) for line in (RECORDINGS / "labels.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    cfg = RelayConfig(url=s.asr_url, api_key=s.model_api_key, model=s.asr_model,
                      language_bias=s.language_bias, keywords=["udhaar", "khata", "UPI"])
    out = []
    for case in labels:
        text = await transcribe(RECORDINGS / case["clip"], cfg)
        print(f"{case['id']}: {text}")
        out.append({"id": case["id"], "text": text})
    (RECORDINGS / "transcripts.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
