"""Draft a SOAP note from a redacted transcript."""

from __future__ import annotations

from dataclasses import dataclass

from scribe.config import Effort
from scribe.llm import ClaudeClient
from scribe.notes.citations import validate_citations
from scribe.notes.schemas import DraftNote, SoapNote

SYSTEM = """\
You write SOAP notes from transcripts of doctor-patient conversations for a software \
demo. The conversations are synthetic. Personal details in the transcript have been \
replaced with placeholders such as [PERSON_1] or [DATE_OF_BIRTH_1]. Copy a placeholder \
exactly if you need it, and never guess what it stands for."""

INSTRUCTIONS = """\
Write a SOAP note for the encounter in the transcript below.

Rules:
- Use only what was said in the transcript. Do not add diagnoses, findings, medications, \
doses, tests or advice that nobody said. If something was mentioned but not done (a drug \
that was considered but not given, a supplement not started), say exactly that.
- Write each statement as one short sentence in plain clinical style. One fact, or a \
tight group of related facts, per statement.
- Give every statement source_segment_ids: the IDs of the transcript segments that \
contain its information, written exactly as shown (s1, s2, ...). Cite every segment you \
relied on and no others. A doctor's question and the patient's answer are often both \
needed.
- Subjective: what the patient reports: symptoms and their timeline, relevant history, \
current medications and adherence, allergies, relevant social details.
- Objective: what the clinician observed or measured at this visit, and test results \
reported in the conversation: vitals, examination findings, lab or point-of-care results.
- Assessment: the clinician's diagnosis or impression, as stated in the conversation.
- Plan: prescriptions with dose and duration, other treatment, tests, referrals, advice, \
work notes, follow-up and reasons to return.
- Leave a section empty rather than inventing content for it.
- Refer to the patient as "the patient". Leave out identification details (name, date of \
birth, phone number, address).

Transcript (one segment per line: [id] SPEAKER: text):
<transcript>
{transcript}
</transcript>"""


@dataclass(frozen=True)
class PromptSegment:
    """A transcript line as the model sees it: redacted text only."""

    id: str
    speaker: str
    text: str


def format_transcript(segments: list[PromptSegment]) -> str:
    return "\n".join(f"[{s.id}] {s.speaker.upper()}: {s.text}" for s in segments)


class NoteDrafter:
    def __init__(self, llm: ClaudeClient, *, model: str, effort: Effort) -> None:
        self.llm = llm
        self.model = model
        self.effort = effort

    def draft(self, segments: list[PromptSegment], *, encounter_id: str | None = None) -> SoapNote:
        draft = self.llm.parse(
            purpose="soap_note",
            model=self.model,
            effort=self.effort,
            system=SYSTEM,
            user=INSTRUCTIONS.format(transcript=format_transcript(segments)),
            output_model=DraftNote,
            encounter_id=encounter_id,
        )
        return validate_citations(draft, [s.id for s in segments])
