"""Pydantic models for the SOAP note.

`DraftNote` is what the model is asked to produce (kept simple so it maps cleanly onto a
JSON schema). `SoapNote` is the validated note the rest of the app uses: four sections
in order, unique statement IDs, and at least one real source segment per statement.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel, Field, model_validator

from scribe.models import Section, SupportLabel

SECTION_ORDER = [Section.SUBJECTIVE, Section.OBJECTIVE, Section.ASSESSMENT, Section.PLAN]


class DraftStatement(BaseModel):
    text: str = Field(description="One clinical statement, written as a single sentence.")
    source_segment_ids: list[str] = Field(
        description="IDs of the transcript segments the statement is based on, e.g. ['s4']."
    )


class DraftNote(BaseModel):
    subjective: list[DraftStatement]
    objective: list[DraftStatement]
    assessment: list[DraftStatement]
    plan: list[DraftStatement]

    def section(self, name: Section) -> list[DraftStatement]:
        statements: list[DraftStatement] = getattr(self, name.value)
        return statements


class NoteStatement(BaseModel):
    id: str
    section: Section
    text: str = Field(min_length=1)
    source_segment_ids: list[str] = Field(min_length=1)
    citations_repaired: bool = False
    support: SupportLabel | None = None
    support_reason: str | None = None


class NoteSection(BaseModel):
    name: Section
    statements: list[NoteStatement]


class RejectedDraft(BaseModel):
    section: Section
    text: str
    cited_ids: list[str]
    reason: str


class SoapNote(BaseModel):
    sections: list[NoteSection]
    rejected: list[RejectedDraft] = []

    @model_validator(mode="after")
    def _check_shape(self) -> SoapNote:
        if [s.name for s in self.sections] != SECTION_ORDER:
            raise ValueError("a note has exactly the four SOAP sections, in order")
        ids = [st.id for st in self.statements()]
        if len(ids) != len(set(ids)):
            raise ValueError("statement IDs must be unique")
        for section in self.sections:
            if any(st.section != section.name for st in section.statements):
                raise ValueError("statement filed under the wrong section")
        return self

    def statements(self) -> Iterator[NoteStatement]:
        for section in self.sections:
            yield from section.statements
