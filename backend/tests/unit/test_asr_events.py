"""Contract tests: the documented Muse realtime event shapes parse into typed models."""

import pytest

from app.asr.events import (
    AudioProgress,
    ErrorEvent,
    HandshakeAck,
    SpeakerEvent,
    SpeechComplete,
    SpeechEnd,
    SpeechStart,
    TranscriptEvent,
    UnknownEvent,
    handshake,
    parse_event,
)
from app.asr.relay import is_calibration_phrase, split_calibration


def test_documented_shapes():
    assert parse_event({"sessionId": "550e8400-e29b-41d4-a716-446655440000"}) == HandshakeAck(
        sessionId="550e8400-e29b-41d4-a716-446655440000")
    e = parse_event({"type": "speechStart", "turnId": 1, "audioProcessedMs": 1200})
    assert isinstance(e, SpeechStart) and e.turn_id == 1
    e = parse_event({"type": "transcript", "transcript": "do kilo", "final": False,
                     "audioProcessedMs": 1500})
    assert isinstance(e, TranscriptEvent) and e.transcript == "do kilo" and not e.final
    e = parse_event({"type": "speechEnd", "turnId": 1, "audioProcessedMs": 3500})
    assert isinstance(e, SpeechEnd)
    e = parse_event({"type": "speechComplete", "turnId": 1, "transcript": "How is the weather?",
                     "audioProcessedMs": 3600})
    assert isinstance(e, SpeechComplete) and e.transcript == "How is the weather?"
    e = parse_event({"type": "speaker", "label": "A", "audioProcessedMs": 1300})
    assert isinstance(e, SpeakerEvent) and e.label == "A"
    e = parse_event({"type": "audioProgress", "audioProcessedMs": 4000})
    assert isinstance(e, AudioProgress)
    e = parse_event({"type": "error", "message": "human-readable message", "sessionId": "x"})
    assert isinstance(e, ErrorEvent) and e.session_id == "x"


def test_unknown_and_malformed_events_are_ignored_not_fatal():
    assert isinstance(parse_event({"type": "somethingNew", "x": 1}), UnknownEvent)
    assert isinstance(parse_event({"type": "speechComplete"}), UnknownEvent)  # missing turnId
    assert isinstance(parse_event({"hello": "world"}), UnknownEvent)


def test_handshake_message():
    h = handshake("sk-test", "muse-voice-transcribe-1.0", ["Hindi", "Telugu"], ["atta"])
    assert h == {
        "authorization": {"accessToken": "Bearer sk-test"},
        "model": "muse-voice-transcribe-1.0",
        "audioEncoding": "PCM_24KHZ",
        "mode": "DIARIZATION",
        "partialMode": "CUMULATIVE",
        "languageBias": ["Hindi", "Telugu"],
        "keywords": ["atta"],
        "emitAudioProgress": False,
    }


def test_calibration_phrase():
    assert is_calibration_phrase("Kirana Kare shuru karo")
    assert is_calibration_phrase("haan, kirana care shuru karo")
    assert not is_calibration_phrase("do kilo atta dena")


@pytest.mark.parametrize(
    ("text", "rest"),
    [
        # Verbatim real Muse output: phrase and first sale merged into one turn (turnId 0).
        (" Kiranakir shurukaro do kilo atta or ek packet doodh business ji pay card hun ",
         "do kilo atta or ek packet doodh business ji pay card hun"),
        ("Kirana Kare shuru karo", ""),
        ("Kirana Kare shuru karo. Ek maggi.", "Ek maggi"),
        ("do kilo atta dena", None),
    ],
)
def test_split_calibration(text, rest):
    assert split_calibration(text) == rest
