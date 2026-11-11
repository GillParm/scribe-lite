from __future__ import annotations

from scribe.audit import AuditLog
from scribe.db import Database
from scribe.llm import ClaudeClient
from scribe.models import SupportLabel
from scribe.notes.checking import (
    CheckResult,
    ClaimChecker,
    Verdict,
    apply_verdicts,
    format_statements,
)
from scribe.notes.citations import validate_citations
from scribe.notes.drafting import PromptSegment
from scribe.notes.schemas import DraftNote, DraftStatement, SoapNote
from tests.fakes import FakeAnthropic, parsed_response

SEGMENTS = [
    PromptSegment("s1", "doctor", "Any fever?"),
    PromptSegment("s2", "patient", "Yes, 38.5 last night."),
    PromptSegment("s3", "doctor", "Azithromycin 500 milligrams daily for five days."),
]


def note() -> SoapNote:
    return validate_citations(
        DraftNote(
            subjective=[
                DraftStatement(text="Fever of 38.5 last night.", source_segment_ids=["s1", "s2"])
            ],
            objective=[],
            assessment=[DraftStatement(text="Viral pharyngitis.", source_segment_ids=["s2"])],
            plan=[
                DraftStatement(
                    text="Azithromycin 500 mg daily for 5 days.", source_segment_ids=["s3"]
                )
            ],
        ),
        [s.id for s in SEGMENTS],
    )


def test_statements_are_listed_with_their_citations() -> None:
    assert format_statements(note()).splitlines() == [
        "[subjective-1] (s1, s2) Fever of 38.5 last night.",
        "[assessment-1] (s2) Viral pharyngitis.",
        "[plan-1] (s3) Azithromycin 500 mg daily for 5 days.",
    ]


def test_verdicts_are_attached_by_id() -> None:
    checked, stats = apply_verdicts(
        note(),
        CheckResult(
            verdicts=[
                Verdict(statement_id="subjective-1", label="supported", reason="Stated in s2."),
                Verdict(
                    statement_id="[ASSESSMENT-1]",
                    label="unsupported",
                    reason="No  diagnosis was\nstated.",
                ),
                Verdict(statement_id="plan-1", label="partially_supported", reason="Dose ok."),
            ]
        ),
    )
    labels = [(s.id, s.support, s.support_reason) for s in checked.statements()]
    assert labels == [
        ("subjective-1", SupportLabel.SUPPORTED, "Stated in s2."),
        ("assessment-1", SupportLabel.UNSUPPORTED, "No diagnosis was stated."),
        ("plan-1", SupportLabel.PARTIALLY_SUPPORTED, "Dose ok."),
    ]
    assert (stats.statements, stats.supported, stats.partially_supported, stats.unsupported) == (
        3,
        1,
        1,
        1,
    )
    assert stats.unsupported_rate == 1 / 3


def test_missing_verdicts_become_unchecked_and_unknown_ids_are_ignored() -> None:
    checked, stats = apply_verdicts(
        note(),
        CheckResult(
            verdicts=[
                Verdict(statement_id="plan-1", label="supported", reason="Stated in s3."),
                Verdict(statement_id="plan-1", label="unsupported", reason="Duplicate."),
                Verdict(statement_id="plan-7", label="unsupported", reason="Invented."),
            ]
        ),
    )
    supports = {s.id: s.support for s in checked.statements()}
    assert supports == {
        "subjective-1": SupportLabel.UNCHECKED,
        "assessment-1": SupportLabel.UNCHECKED,
        "plan-1": SupportLabel.SUPPORTED,  # first verdict wins
    }
    assert stats.unchecked == 2
    assert stats.unknown_ids == ["plan-7"]


def test_original_note_is_not_mutated() -> None:
    original = note()
    apply_verdicts(original, CheckResult(verdicts=[]))
    assert all(s.support is None for s in original.statements())


def test_checker_sends_redacted_transcript_and_statements(db: Database) -> None:
    fake = FakeAnthropic(
        [
            parsed_response(
                CheckResult(
                    verdicts=[
                        Verdict(statement_id="subjective-1", label="supported", reason="s2."),
                        Verdict(
                            statement_id="assessment-1", label="unsupported", reason="Not said."
                        ),
                        Verdict(statement_id="plan-1", label="supported", reason="s3."),
                    ]
                )
            )
        ]
    )
    llm = ClaudeClient(audit=AuditLog(db), client=fake)  # type: ignore[arg-type]
    checker = ClaimChecker(llm, model="claude-opus-5-5", effort="medium")

    checked, stats = checker.check(note(), SEGMENTS, encounter_id=None)

    prompt = fake.calls[0]["messages"][0]["content"]
    assert "[s2] PATIENT: Yes, 38.5 last night." in prompt
    assert "[assessment-1] (s2) Viral pharyngitis." in prompt
    assert fake.calls[0]["output_format"] is CheckResult
    assert stats.unsupported == 1
    assert [s.support for s in checked.statements()][1] == SupportLabel.UNSUPPORTED


def test_empty_note_skips_the_model_call(db: Database) -> None:
    fake = FakeAnthropic([])
    llm = ClaudeClient(audit=AuditLog(db), client=fake)  # type: ignore[arg-type]
    empty = validate_citations(
        DraftNote(subjective=[], objective=[], assessment=[], plan=[]), ["s1"]
    )
    _, stats = ClaimChecker(llm, model="m", effort="low").check(empty, SEGMENTS)
    assert stats.statements == 0
    assert fake.calls == []
