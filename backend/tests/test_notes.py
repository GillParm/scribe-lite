from __future__ import annotations

import pytest
from pydantic import ValidationError

from scribe.models import Section
from scribe.notes.citations import normalise_ids, validate_citations
from scribe.notes.schemas import DraftNote, DraftStatement, NoteSection, NoteStatement, SoapNote

SEGMENTS = [f"s{i}" for i in range(1, 11)]
EN_DASH = chr(0x2013)


def draft(**sections: list[tuple[str, list[str]]]) -> DraftNote:
    empty: dict[str, list[DraftStatement]] = {
        "subjective": [],
        "objective": [],
        "assessment": [],
        "plan": [],
    }
    for name, items in sections.items():
        empty[name] = [DraftStatement(text=t, source_segment_ids=ids) for t, ids in items]
    return DraftNote(**empty)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("s4", ["s4"]),
        ("S4", ["s4"]),
        ("4", ["s4"]),
        ("seg-4", ["s4"]),
        ("[s4]", ["s4"]),
        ("segment 12", ["s12"]),
        ("s3-s5", ["s3", "s4", "s5"]),
        ("s3" + EN_DASH + "s5", ["s3", "s4", "s5"]),
        ("s3 to s5", ["s3", "s4", "s5"]),
        ("s5-s3", []),
        ("s1-s99", []),
        ("the patient", []),
    ],
)
def test_normalise_ids(raw: str, expected: list[str]) -> None:
    assert normalise_ids(raw) == expected


def test_valid_citations_pass_through() -> None:
    note = validate_citations(
        draft(subjective=[("Sore throat for three days.", ["s8"])], plan=[("Rest.", ["s9"])]),
        SEGMENTS,
    )
    statements = list(note.statements())
    assert [(s.id, s.section, s.source_segment_ids) for s in statements] == [
        ("subjective-1", Section.SUBJECTIVE, ["s8"]),
        ("plan-1", Section.PLAN, ["s9"]),
    ]
    assert not any(s.citations_repaired for s in statements)
    assert note.rejected == []


def test_citations_are_repaired() -> None:
    note = validate_citations(
        draft(objective=[("Temp 38.3.", ["S3", "s3", "s42", "4-5"])]), SEGMENTS
    )
    (statement,) = note.statements()
    assert statement.source_segment_ids == ["s3", "s4", "s5"]
    assert statement.citations_repaired is True


def test_statement_without_valid_source_is_rejected() -> None:
    note = validate_citations(
        draft(
            assessment=[
                ("Likely viral.", ["s99"]),
                ("Strep pharyngitis.", ["s7"]),
                ("No source at all.", []),
            ]
        ),
        SEGMENTS,
    )
    assert [s.text for s in note.statements()] == ["Strep pharyngitis."]
    assert [s.id for s in note.statements()] == ["assessment-1"]
    assert [(r.text, r.cited_ids) for r in note.rejected] == [
        ("Likely viral.", ["s99"]),
        ("No source at all.", []),
    ]


def test_blank_statements_are_dropped_silently() -> None:
    note = validate_citations(draft(plan=[("   ", ["s1"])]), SEGMENTS)
    assert list(note.statements()) == []
    assert note.rejected == []


def test_note_always_has_four_sections_in_order() -> None:
    note = validate_citations(draft(), SEGMENTS)
    assert [s.name for s in note.sections] == [
        Section.SUBJECTIVE,
        Section.OBJECTIVE,
        Section.ASSESSMENT,
        Section.PLAN,
    ]


def _statement(id_: str, section: Section, ids: list[str]) -> NoteStatement:
    return NoteStatement(id=id_, section=section, text="x", source_segment_ids=ids)


def test_soap_note_rejects_bad_shapes() -> None:
    plan = NoteSection(name=Section.PLAN, statements=[])
    with pytest.raises(ValidationError):
        SoapNote(sections=[plan])
    with pytest.raises(ValidationError):
        NoteStatement(id="a", section=Section.PLAN, text="x", source_segment_ids=[])
    sections = [
        NoteSection(
            name=Section.SUBJECTIVE, statements=[_statement("a", Section.SUBJECTIVE, ["s1"])]
        ),
        NoteSection(
            name=Section.OBJECTIVE, statements=[_statement("a", Section.OBJECTIVE, ["s1"])]
        ),
        NoteSection(name=Section.ASSESSMENT, statements=[]),
        plan,
    ]
    with pytest.raises(ValidationError, match="unique"):
        SoapNote(sections=sections)
    sections[1] = NoteSection(
        name=Section.OBJECTIVE, statements=[_statement("b", Section.PLAN, ["s1"])]
    )
    with pytest.raises(ValidationError, match="wrong section"):
        SoapNote(sections=sections)


def test_draft_schema_is_plain_json_schema() -> None:
    schema = DraftNote.model_json_schema()
    assert schema["required"] == ["subjective", "objective", "assessment", "plan"]
