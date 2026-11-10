"""The transcriber interface and the clean-up every provider's output goes through."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scribe.schemas import TranscriptSegment


class TranscriptionError(RuntimeError):
    pass


class NoTranscriberError(TranscriptionError):
    """No provider can handle this audio (e.g. not a bundled sample and no Gemini key)."""


@dataclass(frozen=True)
class AudioInput:
    path: Path
    mime_type: str
    sha256: str | None = None
    sample_slug: str | None = None


@dataclass(frozen=True)
class Transcription:
    segments: list[TranscriptSegment]
    provider: str
    model: str | None = None

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio: AudioInput, *, encounter_id: str | None = None) -> Transcription:
        """Return speaker-labelled segments for the recording."""
        ...


def normalize_segments(segments: Iterable[TranscriptSegment]) -> list[TranscriptSegment]:
    """Sort by start time, drop empty turns, fix inverted times and renumber `s1..sN`.

    Provider IDs are never trusted: the note cites segment IDs, so they must be unique
    and stable, and only this function assigns them.
    """
    cleaned = []
    for segment in segments:
        text = " ".join(segment.text.split())
        if not text:
            continue
        start = max(0.0, segment.start)
        end = max(start, segment.end)
        cleaned.append((start, end, segment.speaker, text))
    cleaned.sort(key=lambda item: (item[0], item[1]))
    return [
        TranscriptSegment(id=f"s{index}", speaker=speaker, start=start, end=end, text=text)
        for index, (start, end, speaker, text) in enumerate(cleaned, start=1)
    ]
