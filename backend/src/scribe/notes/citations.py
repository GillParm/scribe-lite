"""Check that every statement cites segments that exist, repairing what can be repaired.

Repairs: IDs are normalised ("S4", "4", "seg-4", "[s4]" -> "s4"), ranges are expanded
("s3-s5" -> s3, s4, s5), duplicates and unknown IDs are dropped. A statement left with
no valid citation is rejected (kept aside with the reason, never shown as part of the
note), because an unsourced sentence is exactly what the UI must not present as fact.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from scribe.notes.schemas import (
    SECTION_ORDER,
    DraftNote,
    NoteSection,
    NoteStatement,
    RejectedDraft,
    SoapNote,
)

_RANGE = re.compile(r"^\D*(\d+)\s*(?:-|\u2013|to|\.\.)\s*\D*(\d+)\D*$")
_SINGLE = re.compile(r"^\D*(\d+)\D*$")
MAX_RANGE = 20


def normalise_ids(raw: str) -> list[str]:
    """Turn one cited ID as the model wrote it into canonical IDs (possibly several)."""
    value = raw.strip().lower()
    match = _RANGE.match(value)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        if low <= high and high - low < MAX_RANGE:
            return [f"s{n}" for n in range(low, high + 1)]
        return []
    match = _SINGLE.match(value)
    return [f"s{int(match.group(1))}"] if match else []


def validate_citations(draft: DraftNote, segment_ids: Collection[str]) -> SoapNote:
    known = set(segment_ids)
    sections: list[NoteSection] = []
    rejected: list[RejectedDraft] = []

    for name in SECTION_ORDER:
        statements: list[NoteStatement] = []
        for item in draft.section(name):
            text = " ".join(item.text.split())
            if not text:
                continue
            valid: list[str] = []
            for raw in item.source_segment_ids:
                for candidate in normalise_ids(raw):
                    if candidate in known and candidate not in valid:
                        valid.append(candidate)
            if not valid:
                rejected.append(
                    RejectedDraft(
                        section=name,
                        text=text,
                        cited_ids=list(item.source_segment_ids),
                        reason="no cited segment exists in the transcript",
                    )
                )
                continue
            statements.append(
                NoteStatement(
                    id=f"{name.value}-{len(statements) + 1}",
                    section=name,
                    text=text,
                    source_segment_ids=valid,
                    citations_repaired=valid != list(item.source_segment_ids),
                )
            )
        sections.append(NoteSection(name=name, statements=statements))
    return SoapNote(sections=sections, rejected=rejected)
