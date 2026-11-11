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
    """Drop empty turns, make times monotonic and renumber `s1..sN`, keeping the order.

    The provider's order is kept even when its timestamps disagree with it: an LLM
    transcriber gets the sequence of turns right far more often than their times, and
    sorting by bad times scrambles the conversation. Provider IDs are never trusted: the
    note cites segment IDs, so they must be unique and stable, and only this function
    assigns them.
    """
    cleaned: list[TranscriptSegment] = []
    previous_start = 0.0
    for segment in segments:
        text = " ".join(segment.text.split())
        if not text:
            continue
        start = max(previous_start, segment.start)
        end = max(start, segment.end)
        previous_start = start
        cleaned.append(
            TranscriptSegment(
                id=f"s{len(cleaned) + 1}",
                speaker=segment.speaker,
                start=start,
                end=end,
                text=text,
            )
        )
    return cleaned
