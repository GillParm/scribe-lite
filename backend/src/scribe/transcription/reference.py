"""Serves the scripted ground truth for the bundled samples, so they need no API key."""

from __future__ import annotations

from scribe.samples import Sample
from scribe.transcription.base import AudioInput, NoTranscriberError, Transcription


class ReferenceTranscriber:
    name = "reference"

    def __init__(self, samples: list[Sample]) -> None:
        self._by_slug = {s.slug: s for s in samples}
        self._by_hash = {s.transcript.audio_sha256: s for s in samples}

    def find(self, audio: AudioInput) -> Sample | None:
        """Match on the sample slug, or on the audio hash for a re-uploaded sample file."""
        if audio.sample_slug and audio.sample_slug in self._by_slug:
            return self._by_slug[audio.sample_slug]
        if audio.sha256:
            return self._by_hash.get(audio.sha256)
        return None

    def transcribe(self, audio: AudioInput, *, encounter_id: str | None = None) -> Transcription:
        sample = self.find(audio)
        if sample is None:
            raise NoTranscriberError("audio is not one of the bundled samples")
        segments = [s.model_copy() for s in sample.transcript.segments]
        return Transcription(segments=segments, provider=self.name, model=None)
