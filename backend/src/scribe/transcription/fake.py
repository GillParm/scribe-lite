"""A transcriber that returns canned segments. For tests."""

from __future__ import annotations

from scribe.schemas import TranscriptSegment
from scribe.transcription.base import AudioInput, Transcription, normalize_segments


class FakeTranscriber:
    name = "fake"

    def __init__(self, segments: list[TranscriptSegment]) -> None:
        self.segments = segments
        self.calls: list[AudioInput] = []

    def transcribe(self, audio: AudioInput, *, encounter_id: str | None = None) -> Transcription:
        self.calls.append(audio)
        return Transcription(
            segments=normalize_segments(self.segments), provider=self.name, model="fake-1"
        )
