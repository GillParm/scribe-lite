"""The processing pipeline for one encounter.

audio -> transcript -> redacted transcript -> SOAP draft -> citation check -> claim check

Each step commits its results and moves the encounter's status forward, so the UI can
show progress and a failure leaves everything up to the failed step in place.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property

from sqlalchemy import delete

from scribe.audit import AuditLog
from scribe.config import TranscriberName
from scribe.db import Database
from scribe.models import (
    Encounter,
    EncounterStatus,
    PiiEntity,
    RejectedStatement,
    Segment,
    Statement,
)
from scribe.notes.checking import CheckStats, ClaimChecker
from scribe.notes.drafting import NoteDrafter, PromptSegment
from scribe.notes.schemas import SoapNote
from scribe.redaction import RedactedTranscript, Redactor
from scribe.storage import AudioStore
from scribe.transcription import AudioInput, NoTranscriberError, Transcriber, Transcription
from scribe.transcription.reference import ReferenceTranscriber

log = logging.getLogger(__name__)


class MissingConfigurationError(RuntimeError):
    pass


@dataclass
class Services:
    db: Database
    store: AudioStore
    audit: AuditLog
    reference: ReferenceTranscriber
    redactor_factory: Callable[[], Redactor]
    transcriber_mode: TranscriberName = "auto"
    gemini: Transcriber | None = None
    fake: Transcriber | None = None
    drafter: NoteDrafter | None = None
    checker: ClaimChecker | None = None

    @cached_property
    def redactor(self) -> Redactor:
        # Loading spaCy takes a couple of seconds; do it once, on first use.
        return self.redactor_factory()

    def choose_transcriber(self, audio: AudioInput) -> Transcriber:
        mode = self.transcriber_mode
        if mode == "fake" and self.fake is not None:
            return self.fake
        if mode == "reference" or (mode == "auto" and self.reference.find(audio)):
            return self.reference
        if mode in ("auto", "gemini") and self.gemini is not None:
            return self.gemini
        raise NoTranscriberError(
            "No transcriber can handle this audio. Set GEMINI_API_KEY to transcribe new "
            "recordings; the bundled samples work without it."
        )


@dataclass(frozen=True)
class PipelineResult:
    encounter_id: str
    ok: bool
    error: str | None = None
    stats: CheckStats | None = None


class Pipeline:
    def __init__(self, services: Services) -> None:
        self.services = services

    @property
    def db(self) -> Database:
        return self.services.db

    def process(self, encounter_id: str) -> PipelineResult:
        """Run every step. Never raises: failures are stored on the encounter."""
        try:
            stats = self._run(encounter_id)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:500]
            log.exception("processing failed for encounter %s", encounter_id)
            self._set_status(encounter_id, EncounterStatus.FAILED, error=message)
            return PipelineResult(encounter_id, ok=False, error=message)
        return PipelineResult(encounter_id, ok=True, stats=stats)

    # -- steps ---------------------------------------------------------------------

    def _run(self, encounter_id: str) -> CheckStats:
        audio = self._start(encounter_id)
        transcription = self.services.choose_transcriber(audio).transcribe(
            audio, encounter_id=encounter_id
        )
        self._save_transcript(encounter_id, transcription)

        redacted = self.services.redactor.redact_segments(transcription.segments)
        self._save_redaction(encounter_id, redacted)

        drafter, checker = self.services.drafter, self.services.checker
        if drafter is None or checker is None:
            raise MissingConfigurationError("Set ANTHROPIC_API_KEY to generate notes.")
        prompt_segments = [
            PromptSegment(r.segment.id, r.segment.speaker.value, r.redacted_text)
            for r in redacted.segments
        ]
        note = drafter.draft(prompt_segments, encounter_id=encounter_id)
        self._set_status(encounter_id, EncounterStatus.CHECKING, note_model=drafter.model)

        checked, stats = checker.check(note, prompt_segments, encounter_id=encounter_id)
        self._save_note(encounter_id, checked, check_model=checker.model)
        return stats

    def _start(self, encounter_id: str) -> AudioInput:
        with self.db.session() as session:
            encounter = session.get_one(Encounter, encounter_id)
            if encounter.audio_path is None:
                raise FileNotFoundError("the audio for this encounter has been deleted")
            encounter.status = EncounterStatus.TRANSCRIBING
            encounter.error = None
            return AudioInput(
                path=self.services.store.path(encounter.audio_path),
                mime_type=encounter.audio_mime or "application/octet-stream",
                sha256=encounter.audio_sha256,
                sample_slug=encounter.sample_slug,
            )

    def _save_transcript(self, encounter_id: str, transcription: Transcription) -> None:
        with self.db.session() as session:
            for table in (Statement, RejectedStatement, PiiEntity, Segment):
                session.execute(delete(table).where(table.encounter_id == encounter_id))
            encounter = session.get_one(Encounter, encounter_id)
            encounter.transcriber = transcription.label
            encounter.status = EncounterStatus.REDACTING
            if transcription.segments:
                encounter.duration_seconds = max(s.end for s in transcription.segments)
            session.add_all(
                Segment(
                    encounter_id=encounter_id,
                    segment_id=s.id,
                    position=index,
                    speaker=s.speaker,
                    start_s=s.start,
                    end_s=s.end,
                    text=s.text,
                )
                for index, s in enumerate(transcription.segments)
            )

    def _save_redaction(self, encounter_id: str, redacted: RedactedTranscript) -> None:
        by_id = {r.segment.id: r for r in redacted.segments}
        with self.db.session() as session:
            encounter = session.get_one(Encounter, encounter_id)
            for segment in encounter.segments:
                result = by_id[segment.segment_id]
                segment.redacted_text = result.redacted_text
                segment.redaction_spans = [span.public() for span in result.spans]
            session.add_all(
                PiiEntity(
                    encounter_id=encounter_id,
                    placeholder=entry.placeholder,
                    entity_type=entry.entity_type,
                    value=entry.value,
                    variants=entry.variants,
                    occurrences=entry.occurrences,
                )
                for entry in redacted.vault.entries.values()
            )
            encounter.status = EncounterStatus.DRAFTING

    def _save_note(self, encounter_id: str, note: SoapNote, *, check_model: str) -> None:
        with self.db.session() as session:
            session.add_all(
                Statement(
                    encounter_id=encounter_id,
                    statement_id=s.id,
                    section=s.section,
                    position=index,
                    text=s.text,
                    source_segment_ids=s.source_segment_ids,
                    citations_repaired=s.citations_repaired,
                    support=s.support,
                    support_reason=s.support_reason,
                )
                for index, s in enumerate(note.statements())
            )
            session.add_all(
                RejectedStatement(
                    encounter_id=encounter_id,
                    section=r.section,
                    text=r.text,
                    cited_ids=r.cited_ids,
                    reason=r.reason,
                )
                for r in note.rejected
            )
            encounter = session.get_one(Encounter, encounter_id)
            encounter.check_model = check_model
            encounter.status = EncounterStatus.READY

    def _set_status(
        self,
        encounter_id: str,
        status: EncounterStatus,
        *,
        error: str | None = None,
        note_model: str | None = None,
    ) -> None:
        with self.db.session() as session:
            encounter = session.get(Encounter, encounter_id)
            if encounter is None:
                return
            encounter.status = status
            encounter.error = error
            if note_model:
                encounter.note_model = note_model
