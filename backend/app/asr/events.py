"""Typed Muse Voice Transcribe realtime events (https://dev.meta.ai/docs/speech-to-text).

The handshake ack is the only frame without a `type`. Unknown event types must be ignored,
so they parse into `UnknownEvent` rather than failing.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class _Event(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class HandshakeAck(_Event):
    session_id: str = Field(alias="sessionId")


class SpeechStart(_Event):
    type: Literal["speechStart"]
    turn_id: int = Field(alias="turnId")
    audio_processed_ms: int | None = Field(default=None, alias="audioProcessedMs")


class TranscriptEvent(_Event):
    """Partial (or, in PUSH_TO_TALK, final) text. Carries no turnId: it belongs to the most
    recent speechStart."""

    type: Literal["transcript"]
    transcript: str = ""
    final: bool = False
    audio_processed_ms: int | None = Field(default=None, alias="audioProcessedMs")


class SpeechEnd(_Event):
    type: Literal["speechEnd"]
    turn_id: int = Field(alias="turnId")
    audio_processed_ms: int | None = Field(default=None, alias="audioProcessedMs")


class SpeechComplete(_Event):
    type: Literal["speechComplete"]
    turn_id: int = Field(alias="turnId")
    transcript: str = ""
    audio_processed_ms: int | None = Field(default=None, alias="audioProcessedMs")


class SpeakerEvent(_Event):
    """Diarization label (A, B, …), session-scoped; applies to the most recent turn."""

    type: Literal["speaker"]
    label: str
    audio_processed_ms: int | None = Field(default=None, alias="audioProcessedMs")


class AudioProgress(_Event):
    type: Literal["audioProgress"]
    audio_processed_ms: int = Field(alias="audioProcessedMs")


class ErrorEvent(_Event):
    type: Literal["error"]
    message: str = ""
    session_id: str | None = Field(default=None, alias="sessionId")


KnownEvent = Annotated[
    SpeechStart | TranscriptEvent | SpeechEnd | SpeechComplete | SpeakerEvent | AudioProgress
    | ErrorEvent,
    Field(discriminator="type"),
]
_adapter: TypeAdapter[Any] = TypeAdapter(KnownEvent)


class UnknownEvent(_Event):
    type: str | None = None
    raw: dict[str, Any] = {}


MuseEvent = (
    HandshakeAck | SpeechStart | TranscriptEvent | SpeechEnd | SpeechComplete | SpeakerEvent
    | AudioProgress | ErrorEvent | UnknownEvent
)


def parse_event(data: dict[str, Any]) -> MuseEvent:
    if "type" not in data:
        if "sessionId" in data:
            return HandshakeAck.model_validate(data)
        return UnknownEvent(raw=data)
    try:
        return _adapter.validate_python(data)  # type: ignore[no-any-return]
    except ValidationError:
        return UnknownEvent(type=str(data.get("type")), raw=data)


def handshake(api_key: str, model: str, language_bias: list[str], keywords: list[str],
              mode: str = "DIARIZATION") -> dict[str, Any]:
    return {
        "authorization": {"accessToken": f"Bearer {api_key}"},
        "model": model,
        "audioEncoding": "PCM_24KHZ",
        "mode": mode,
        "partialMode": "CUMULATIVE",
        "languageBias": language_bias,
        "keywords": keywords,
        "emitAudioProgress": False,
    }
