"""The bundled synthetic samples: scripts, voiced audio, reference transcripts and facts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from scribe.models import Section, Speaker
from scribe.schemas import TranscriptSegment

PiiType = Literal["PERSON", "DATE_OF_BIRTH", "PHONE_NUMBER", "ADDRESS"]


class ScriptTurn(BaseModel):
    speaker: Speaker
    text: str


class KnownPii(BaseModel):
    type: PiiType
    value: str


class SampleScript(BaseModel):
    slug: str
    title: str
    synthetic: bool
    notice: str
    voices: dict[Speaker, str]
    turns: list[ScriptTurn]
    pii: list[KnownPii]


class ReferenceTranscript(BaseModel):
    slug: str
    source: str
    audio: str
    audio_sha256: str
    duration_seconds: float
    segments: list[TranscriptSegment]


class ReferenceFact(BaseModel):
    id: str
    section: Section
    fact: str


class ReferenceFacts(BaseModel):
    slug: str
    written: str
    facts: list[ReferenceFact]


@dataclass(frozen=True)
class Sample:
    directory: Path
    script: SampleScript
    transcript: ReferenceTranscript
    facts: ReferenceFacts

    @property
    def slug(self) -> str:
        return self.script.slug

    @property
    def title(self) -> str:
        return self.script.title

    @property
    def audio_path(self) -> Path:
        return self.directory / self.transcript.audio

    @property
    def outputs_dir(self) -> Path:
        return self.directory / "outputs"


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_sample(directory: Path) -> Sample:
    return Sample(
        directory=directory,
        script=SampleScript.model_validate(_read(directory / "script.json")),
        transcript=ReferenceTranscript.model_validate(_read(directory / "transcript.json")),
        facts=ReferenceFacts.model_validate(_read(directory / "reference_facts.json")),
    )


def load_samples(samples_dir: Path) -> list[Sample]:
    """Every sample under `samples_dir`, in slug order. Missing directory -> empty list."""
    if not samples_dir.is_dir():
        return []
    return [
        load_sample(path.parent)
        for path in sorted(samples_dir.glob("*/transcript.json"))
        if (path.parent / "script.json").exists()
    ]
