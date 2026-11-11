"""Scoring generated notes against reference facts.

Coverage: Claude reads the note and the reference facts and judges each fact present,
partial or absent. Coverage = (present + 0.5 * partial) / facts.

Unsupported-claim rate: unsupported statements / all statements, from the claim check.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from scribe.config import Effort
from scribe.llm import ClaudeClient
from scribe.samples import ReferenceFact

SYSTEM = """\
You grade clinical notes against lists of reference facts for a software evaluation. The \
notes and facts are synthetic. Personal details in the notes appear as placeholders such \
as [PERSON_1]; they are never part of a fact."""

INSTRUCTIONS = """\
For each reference fact, decide whether the SOAP note below states it.

- present: the note states the fact with the same clinical meaning. Wording may differ \
and the fact may be spread over several statements, but numbers, doses, frequencies, \
durations, body sides and negations must match.
- partial: the note states part of the fact but leaves out or gets wrong a detail (for \
example the drug but not the dose, or the finding but not the side).
- absent: the note does not state the fact, or contradicts it.

Judge only whether the information is in the note, not which section it is filed under. \
Return exactly one verdict per fact, with the IDs of the statements that carry it and \
one short sentence of reasoning.

Reference facts:
<facts>
{facts}
</facts>

SOAP note ([statement_id] text):
<note>
{note}
</note>"""

FactLabel = Literal["present", "partial", "absent"]


class FactVerdict(BaseModel):
    fact_id: str
    verdict: FactLabel
    statement_ids: list[str] = Field(description="Statements that carry the fact; may be empty.")
    reason: str


class CoverageJudgement(BaseModel):
    verdicts: list[FactVerdict]


@dataclass(frozen=True)
class NoteStatementText:
    id: str
    text: str


@dataclass(frozen=True)
class CoverageScore:
    facts: int
    present: int
    partial: int
    absent: int
    missing_verdicts: list[str]
    verdicts: list[FactVerdict]

    @property
    def coverage(self) -> float:
        return (self.present + 0.5 * self.partial) / self.facts if self.facts else 0.0


def score_coverage(facts: list[ReferenceFact], judgement: CoverageJudgement) -> CoverageScore:
    """One verdict per fact; a fact the judge skipped counts as absent and is listed."""
    by_id: dict[str, FactVerdict] = {}
    for verdict in judgement.verdicts:
        key = verdict.fact_id.strip().strip("[]").upper()
        by_id.setdefault(key, verdict)
    counts = {"present": 0, "partial": 0, "absent": 0}
    missing = []
    ordered = []
    for fact in facts:
        found = by_id.get(fact.id.upper())
        if found is None:
            missing.append(fact.id)
            found = FactVerdict(
                fact_id=fact.id, verdict="absent", statement_ids=[], reason="No verdict returned."
            )
        counts[found.verdict] += 1
        ordered.append(found)
    return CoverageScore(
        facts=len(facts),
        present=counts["present"],
        partial=counts["partial"],
        absent=counts["absent"],
        missing_verdicts=missing,
        verdicts=ordered,
    )


def unsupported_rate(labels: Iterable[str | None]) -> tuple[int, int, float]:
    """(unsupported count, statement count, rate)."""
    values = list(labels)
    unsupported = sum(1 for label in values if label == "unsupported")
    return unsupported, len(values), (unsupported / len(values) if values else 0.0)


class CoverageJudge:
    def __init__(self, llm: ClaudeClient, *, model: str, effort: Effort) -> None:
        self.llm = llm
        self.model = model
        self.effort = effort

    def judge(
        self, facts: list[ReferenceFact], statements: list[NoteStatementText]
    ) -> CoverageScore:
        judgement = self.llm.parse(
            purpose="eval_coverage",
            model=self.model,
            effort=self.effort,
            system=SYSTEM,
            user=INSTRUCTIONS.format(
                facts="\n".join(f"[{f.id}] {f.fact}" for f in facts),
                note="\n".join(f"[{s.id}] {s.text}" for s in statements),
            ),
            output_model=CoverageJudgement,
        )
        return score_coverage(facts, judgement)
