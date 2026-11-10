"""Pydantic types shared across the pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field

from scribe.models import Speaker


class TranscriptSegment(BaseModel):
    """One speaker turn. `id` is unique within a transcript (`s1`, `s2`, ...)."""

    id: str
    speaker: Speaker
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str
