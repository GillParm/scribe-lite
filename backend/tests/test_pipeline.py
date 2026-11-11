from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from scribe.audit import AuditLog
from scribe.config import REPO_ROOT
from scribe.db import Database
from scribe.encounters import create_encounter, export_encounter, import_sample
from scribe.llm import ClaudeClient
from scribe.models import Encounter, EncounterSource, EncounterStatus, ModelCall, SupportLabel
from scribe.notes.checking import CheckResult, ClaimChecker, Verdict
from scribe.notes.drafting import NoteDrafter
from scribe.notes.schemas import DraftNote, DraftStatement
from scribe.pipeline import Pipeline, Services
from scribe.redaction import Redactor
from scribe.samples import Sample, load_samples
from scribe.storage import AudioStore
from scribe.transcription import AudioInput
from scribe.transcription.reference import ReferenceTranscriber
from tests.fakes import FakeAnthropic, parsed_response

SAMPLES = load_samples(REPO_ROOT / "samples")
SORE_THROAT = next(s for s in SAMPLES if s.slug == "sore-throat")
MAX = 10 * 1024 * 1024

DRAFT = DraftNote(
    subjective=[
        DraftStatement(text="Sore throat for three days.", source_segment_ids=["s8"]),
        DraftStatement(text="Seen by [PERSON_2] today.", source_segment_ids=["S1"]),
    ],
    objective=[DraftStatement(text="Rapid strep test positive.", source_segment_ids=["s23"])],
    assessment=[
        DraftStatement(text="Strep pharyngitis.", source_segment_ids=["s25"]),
        DraftStatement(text="Possible mono.", source_segment_ids=["s99"]),
    ],
    plan=[DraftStatement(text="Amoxicillin 500 mg.", source_segment_ids=["s25"])],
)
VERDICTS = CheckResult(
    verdicts=[
        Verdict(statement_id="subjective-1", label="supported", reason="Stated in s8."),
        Verdict(statement_id="subjective-2", label="supported", reason="Stated in s1."),
        Verdict(statement_id="objective-1", label="supported", reason="Stated in s23."),
        Verdict(statement_id="assessment-1", label="supported", reason="Stated in s25."),
        Verdict(statement_id="plan-1", label="unsupported", reason="Azithromycin was prescribed."),
    ]
)


def make_services(
    db: Database, store: AudioStore, redactor: Redactor, outcomes: list[Any] | None
) -> tuple[Services, FakeAnthropic | None]:
    services = Services(
        db=db,
        store=store,
        audit=AuditLog(db),
        reference=ReferenceTranscriber(SAMPLES),
        redactor_factory=lambda: redactor,
    )
    fake = None
    if outcomes is not None:
        fake = FakeAnthropic(outcomes)
        llm = ClaudeClient(audit=services.audit, client=fake)  # type: ignore[arg-type]
        services.drafter = NoteDrafter(llm, model="claude-opus-5-5", effort="medium")
        services.checker = ClaimChecker(llm, model="claude-opus-5-5", effort="medium")
    return services, fake


def add_sample(db: Database, store: AudioStore, sample: Sample) -> str:
    with sample.audio_path.open("rb") as audio:
        return create_encounter(
            db,
            store,
            title=sample.title,
            source=EncounterSource.SAMPLE,
            audio=audio,
            mime_type="audio/mp4",
            max_bytes=MAX,
            sample_slug=sample.slug,
        )


def test_full_pipeline_on_a_sample(db: Database, store: AudioStore, redactor: Redactor) -> None:
    services, _ = make_services(
        db, store, redactor, [parsed_response(DRAFT), parsed_response(VERDICTS)]
    )
    encounter_id = add_sample(db, store, SORE_THROAT)

    result = Pipeline(services).process(encounter_id)

    assert result.ok, result.error
    assert result.stats is not None and result.stats.unsupported == 1
    with db.session() as session:
        encounter = session.get_one(Encounter, encounter_id)
        assert encounter.status == EncounterStatus.READY
        assert encounter.transcriber == "reference"
        assert (encounter.note_model, encounter.check_model) == ("claude-opus-5-5",) * 2
        assert len(encounter.segments) == 33
        assert encounter.segments[1].redacted_text == (
            "Sure. It's [PERSON_2], and my date of birth is [DATE_OF_BIRTH_1]."
        )
        assert {p.entity_type for p in encounter.pii_entities} == {
            "PERSON",
            "DATE_OF_BIRTH",
            "PHONE_NUMBER",
            "ADDRESS",
        }
        statements = {s.statement_id: s for s in encounter.statements}
        assert statements["subjective-2"].source_segment_ids == ["s1"]
        assert statements["subjective-2"].citations_repaired is True
        assert statements["plan-1"].support == SupportLabel.UNSUPPORTED
        assert [r.text for r in encounter.rejected_statements] == ["Possible mono."]
        calls = session.scalars(select(ModelCall.purpose).order_by(ModelCall.id)).all()
        assert calls == ["soap_note", "claim_check"]


def test_no_known_pii_reaches_the_model(
    db: Database, store: AudioStore, redactor: Redactor
) -> None:
    for sample in SAMPLES:
        services, fake = make_services(
            db, store, redactor, [parsed_response(DRAFT), parsed_response(VERDICTS)]
        )
        Pipeline(services).process(add_sample(db, store, sample))
        assert fake is not None
        sent = " ".join(str(call["system"]) + str(call["messages"]) for call in fake.calls)
        for item in sample.script.pii:
            assert item.value not in sent, (sample.slug, item.value)


def test_missing_anthropic_key_stops_after_redaction(
    db: Database, store: AudioStore, redactor: Redactor
) -> None:
    services, _ = make_services(db, store, redactor, outcomes=None)
    encounter_id = add_sample(db, store, SORE_THROAT)

    result = Pipeline(services).process(encounter_id)

    assert not result.ok
    with db.session() as session:
        encounter = session.get_one(Encounter, encounter_id)
        assert encounter.status == EncounterStatus.FAILED
        assert "ANTHROPIC_API_KEY" in (encounter.error or "")
        assert len(encounter.segments) == 33  # earlier steps are kept
        assert encounter.segments[0].redacted_text is not None


def test_unknown_audio_without_gemini_fails_clearly(
    db: Database, store: AudioStore, redactor: Redactor
) -> None:
    services, _ = make_services(db, store, redactor, outcomes=[])
    encounter_id = create_encounter(
        db,
        store,
        title="Upload",
        source=EncounterSource.UPLOAD,
        audio=io.BytesIO(b"not a sample"),
        mime_type="audio/wav",
        max_bytes=MAX,
    )
    result = Pipeline(services).process(encounter_id)
    assert not result.ok
    assert "GEMINI_API_KEY" in (result.error or "")


def test_deleted_audio_fails_clearly(db: Database, store: AudioStore, redactor: Redactor) -> None:
    services, _ = make_services(db, store, redactor, outcomes=[])
    encounter_id = add_sample(db, store, SORE_THROAT)
    with db.session() as session:
        session.get_one(Encounter, encounter_id).audio_path = None
    result = Pipeline(services).process(encounter_id)
    assert not result.ok
    assert "deleted" in (result.error or "")


def test_reprocessing_replaces_previous_results(
    db: Database, store: AudioStore, redactor: Redactor
) -> None:
    outcomes = [parsed_response(DRAFT), parsed_response(VERDICTS)] * 2
    services, _ = make_services(db, store, redactor, outcomes)
    encounter_id = add_sample(db, store, SORE_THROAT)
    pipeline = Pipeline(services)
    assert pipeline.process(encounter_id).ok
    assert pipeline.process(encounter_id).ok
    with db.session() as session:
        encounter = session.get_one(Encounter, encounter_id)
        assert len(encounter.segments) == 33
        assert len(encounter.statements) == 5


def test_export_then_import_round_trips(
    db: Database, store: AudioStore, redactor: Redactor, tmp_path: Path
) -> None:
    services, _ = make_services(
        db, store, redactor, [parsed_response(DRAFT), parsed_response(VERDICTS)]
    )
    original = add_sample(db, store, SORE_THROAT)
    assert Pipeline(services).process(original).ok
    exported = export_encounter(db, original)

    copy = import_sample(db, AudioStore(tmp_path / "other"), SORE_THROAT, exported, MAX)

    again = export_encounter(db, copy)
    for key in ("segments", "pii_entities", "statements", "rejected_statements"):
        assert again[key] == exported[key], key
    assert [c["prompt_sha256"] for c in again["model_calls"]] == [
        c["prompt_sha256"] for c in exported["model_calls"]
    ]
    with db.session() as session:
        origins = session.scalars(
            select(ModelCall.origin).where(ModelCall.encounter_id == copy)
        ).all()
        assert origins == ["sample_import", "sample_import"]
        assert session.scalar(select(func.count(ModelCall.id))) == 4


@pytest.mark.parametrize("mode", ["reference", "auto"])
def test_samples_use_the_reference_transcriber(
    db: Database, store: AudioStore, redactor: Redactor, mode: str
) -> None:
    services, _ = make_services(db, store, redactor, outcomes=None)
    services.transcriber_mode = mode  # type: ignore[assignment]

    audio = AudioInput(
        path=SORE_THROAT.audio_path, mime_type="audio/mp4", sample_slug="sore-throat"
    )
    assert services.choose_transcriber(audio) is services.reference
