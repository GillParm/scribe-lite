"""Speech-to-text behind a small provider interface."""

from scribe.transcription.base import (
    AudioInput,
    NoTranscriberError,
    Transcriber,
    Transcription,
    TranscriptionError,
    normalize_segments,
)

__all__ = [
    "AudioInput",
    "NoTranscriberError",
    "Transcriber",
    "Transcription",
    "TranscriptionError",
    "normalize_segments",
]
