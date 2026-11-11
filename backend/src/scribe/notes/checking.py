"""Second pass: check every note statement against the transcript.

A separate model call reads the redacted transcript and each statement with the
segments it cites, and labels it supported, partially_supported or unsupported with a
one-line reason. The UI flags anything that is not fully supported. Statements the
checker skips are marked `unchecked` rather than assumed fine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from scribe.config import Effort
from scribe.llm import ClaudeClient
from scribe.models import SupportLabel
from scribe.notes.drafting import PromptSegment, format_transcript
from scribe.notes.schemas import SoapNote

SYSTEM = """\
You review clinical notes for a software demo. You compare each statement in a SOAP \
note with the conversation it was written from and say whether the conversation \
supports it. The conversations are synthetic. Personal details appear as placeholders \
such as [PERSON_1]; treat a placeholder as matching the same placeholder in the \
transcript."""

INSTRUCTIONS = """\
Check every statement of the SOAP note below against the transcript.

For each statement return its statement_id, a label and a reason.
- supported: the transcript states everything in the statement. Paraphrase is fine, but \
numbers, doses, frequencies, durations, body sides and negations must match.
- partially_supported: nothing in the statement contradicts the transcript, but a \
detail is not in the transcript or is overstated, or the support is only in segments \
the statement does not cite.
- unsupported: the transcript does not say this, or any part of it contradicts the \
transcript. One wrong number, dose, frequency, duration, body side or negation makes \
the whole statement unsupported, even if the rest is right. A diagnosis, finding or \
instruction that nobody said aloud is unsupported, even if it is clinically reasonable.

Read the cited segments first, then the rest of the transcript. Keep each reason to one \
short sentence. For supported statements, name the segments (for example "Stated in \
s4."). For the others, name what is not supported.

Transcript (one segment per line: [id] SPEAKER: text):
<transcript>
{transcript}
</transcript>

Statements ([statement_id] (cited segments) text):
<statements>
{statements}
</statements>"""


class Verdict(BaseModel):
    statement_id: str
    label: Literal["supported", "partially_supported", "unsupported"]
    reason: str = Field(description="One short sentence.")


class CheckResult(BaseModel):
    verdicts: list[Verdict]


@dataclass(frozen=True)
class CheckStats:
    statements: int
    supported: int
    partially_supported: int
    unsupported: int
    unchecked: int
    unknown_ids: list[str]

    @property
    def unsupported_rate(self) -> float:
        return self.unsupported / self.statements if self.statements else 0.0


def format_statements(note: SoapNote) -> str:
    return "\n".join(
        f"[{s.id}] ({', '.join(s.source_segment_ids)}) {s.text}" for s in note.statements()
    )


def _clean_id(raw: str) -> str:
    return raw.strip().strip("[]()").strip().lower()


def apply_verdicts(note: SoapNote, result: CheckResult) -> tuple[SoapNote, CheckStats]:
    """Attach verdicts to statements. Unknown IDs are ignored; the first verdict wins."""
    known = {s.id for s in note.statements()}
    verdicts: dict[str, Verdict] = {}
    unknown: list[str] = []
    for verdict in result.verdicts:
        statement_id = _clean_id(verdict.statement_id)
        if statement_id not in known:
            unknown.append(verdict.statement_id)
        elif statement_id not in verdicts:
            verdicts[statement_id] = verdict

    checked = note.model_copy(deep=True)
    counts = dict.fromkeys(SupportLabel, 0)
    for statement in checked.statements():
        found = verdicts.get(statement.id)
        if found is None:
            statement.support = SupportLabel.UNCHECKED
            statement.support_reason = "The checker returned no verdict for this statement."
        else:
            statement.support = SupportLabel(found.label)
            statement.support_reason = " ".join(found.reason.split())
        counts[statement.support] += 1

    stats = CheckStats(
        statements=sum(counts.values()),
        supported=counts[SupportLabel.SUPPORTED],
        partially_supported=counts[SupportLabel.PARTIALLY_SUPPORTED],
        unsupported=counts[SupportLabel.UNSUPPORTED],
        unchecked=counts[SupportLabel.UNCHECKED],
        unknown_ids=unknown,
    )
    return checked, stats


class ClaimChecker:
    def __init__(self, llm: ClaudeClient, *, model: str, effort: Effort) -> None:
        self.llm = llm
        self.model = model
        self.effort = effort

    def check(
        self,
        note: SoapNote,
        segments: list[PromptSegment],
        *,
        encounter_id: str | None = None,
    ) -> tuple[SoapNote, CheckStats]:
        if not any(True for _ in note.statements()):
            return apply_verdicts(note, CheckResult(verdicts=[]))
        result = self.llm.parse(
            purpose="claim_check",
            model=self.model,
            effort=self.effort,
            system=SYSTEM,
            user=INSTRUCTIONS.format(
                transcript=format_transcript(segments), statements=format_statements(note)
            ),
            output_model=CheckResult,
            encounter_id=encounter_id,
        )
        return apply_verdicts(note, result)
