from __future__ import annotations

import pytest

from scribe.audit import AuditLog
from scribe.db import Database
from scribe.evaluation import (
    CoverageJudge,
    CoverageJudgement,
    FactVerdict,
    NoteStatementText,
    score_coverage,
    unsupported_rate,
)
from scribe.llm import ClaudeClient
from scribe.models import Section
from scribe.samples import ReferenceFact
from tests.fakes import FakeAnthropic, parsed_response

FACTS = [
    ReferenceFact(id="F1", section=Section.SUBJECTIVE, fact="Sore throat for three days."),
    ReferenceFact(id="F2", section=Section.PLAN, fact="Azithromycin 500 mg daily for 5 days."),
    ReferenceFact(id="F3", section=Section.PLAN, fact="Salt water gargles."),
    ReferenceFact(id="F4", section=Section.OBJECTIVE, fact="Rapid strep positive."),
]


def verdict(fact_id: str, label: str) -> FactVerdict:
    return FactVerdict.model_validate(
        {"fact_id": fact_id, "verdict": label, "statement_ids": [], "reason": "r"}
    )


def test_coverage_counts_partial_as_half() -> None:
    score = score_coverage(
        FACTS,
        CoverageJudgement(
            verdicts=[
                verdict("F1", "present"),
                verdict("F2", "partial"),
                verdict("F3", "absent"),
                verdict("F4", "present"),
            ]
        ),
    )
    assert (score.present, score.partial, score.absent) == (2, 1, 1)
    assert score.coverage == pytest.approx(2.5 / 4)
    assert score.missing_verdicts == []


def test_missing_verdicts_count_as_absent_and_duplicates_are_ignored() -> None:
    score = score_coverage(
        FACTS,
        CoverageJudgement(
            verdicts=[
                verdict("[f1]", "present"),
                verdict("F1", "absent"),
                verdict("F2", "present"),
                verdict("F9", "present"),
            ]
        ),
    )
    assert [v.verdict for v in score.verdicts] == ["present", "present", "absent", "absent"]
    assert score.missing_verdicts == ["F3", "F4"]
    assert score.coverage == pytest.approx(0.5)


def test_unsupported_rate() -> None:
    assert unsupported_rate(["supported", "unsupported", "partially_supported", None]) == (
        1,
        4,
        0.25,
    )
    assert unsupported_rate([]) == (0, 0, 0.0)


def test_judge_sends_facts_and_redacted_statements(db: Database) -> None:
    fake = FakeAnthropic(
        [parsed_response(CoverageJudgement(verdicts=[verdict(f.id, "present") for f in FACTS]))]
    )
    judge = CoverageJudge(
        ClaudeClient(audit=AuditLog(db), client=fake),  # type: ignore[arg-type]
        model="claude-opus-5-5",
        effort="medium",
    )
    score = judge.judge(FACTS, [NoteStatementText("plan-1", "[PERSON_1] to gargle.")])
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "[F2] Azithromycin 500 mg daily for 5 days." in prompt
    assert "[plan-1] [PERSON_1] to gargle." in prompt
    assert score.coverage == 1.0
