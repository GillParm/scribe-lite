"""Creating encounters, and moving processed encounters in and out of JSON.

The JSON form is how the bundled samples ship their precomputed outputs: run the real
pipeline once (`scribe samples rebuild`), export, commit, and every fresh install can
import the result without any API key.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from scribe.db import Database
from scribe.models import (
    Encounter,
    EncounterSource,
    EncounterStatus,
    ModelCall,
    PiiEntity,
    RejectedStatement,
    Section,
    Segment,
    Speaker,
    Statement,
    SupportLabel,
)
from scribe.samples import Sample
from scribe.storage import AUDIO_TYPES, AudioStore

log = logging.getLogger(__name__)

EXPORT_VERSION = 1


def create_encounter(
    db: Database,
    store: AudioStore,
    *,
    title: str,
    source: EncounterSource,
    audio: BinaryIO,
    mime_type: str,
    max_bytes: int,
    sample_slug: str | None = None,
) -> str:
    encounter_id = str(uuid.uuid4())
    suffix = AUDIO_TYPES.get(mime_type, ".bin")
    stored = store.save(encounter_id, audio, suffix, max_bytes)
    with db.session() as session:
        session.add(
            Encounter(
                id=encounter_id,
                title=title,
                source=source,
                sample_slug=sample_slug,
                status=EncounterStatus.UPLOADED,
                audio_path=stored.relative_path,
                audio_mime=mime_type,
                audio_sha256=stored.sha256,
                audio_bytes=stored.size,
            )
        )
    return encounter_id


# -- export ------------------------------------------------------------------------


def export_encounter(db: Database, encounter_id: str) -> dict[str, Any]:
    with db.session() as session:
        encounter = session.get_one(Encounter, encounter_id)
        calls = session.scalars(
            select(ModelCall)
            .where(ModelCall.encounter_id == encounter_id)
            .order_by(ModelCall.created_at, ModelCall.id)
        ).all()
        return {
            "version": EXPORT_VERSION,
            "sample_slug": encounter.sample_slug,
            "title": encounter.title,
            "processed_at": encounter.updated_at.isoformat(),
            "transcriber": encounter.transcriber,
            "note_model": encounter.note_model,
            "check_model": encounter.check_model,
            "duration_seconds": encounter.duration_seconds,
            "segments": [
                {
                    "id": s.segment_id,
                    "speaker": s.speaker.value,
                    "start": s.start_s,
                    "end": s.end_s,
                    "text": s.text,
                    "redacted_text": s.redacted_text,
                    "redaction_spans": s.redaction_spans or [],
                }
                for s in encounter.segments
            ],
            "pii_entities": [
                {
                    "placeholder": p.placeholder,
                    "entity_type": p.entity_type,
                    "value": p.value,
                    "variants": p.variants,
                    "occurrences": p.occurrences,
                }
                for p in encounter.pii_entities
            ],
            "statements": [
                {
                    "id": s.statement_id,
                    "section": s.section.value,
                    "text": s.text,
                    "source_segment_ids": s.source_segment_ids,
                    "citations_repaired": s.citations_repaired,
                    "support": s.support.value if s.support else None,
                    "support_reason": s.support_reason,
                }
                for s in encounter.statements
            ],
            "rejected_statements": [
                {
                    "section": r.section.value,
                    "text": r.text,
                    "cited_ids": r.cited_ids,
                    "reason": r.reason,
                }
                for r in encounter.rejected_statements
            ],
            "model_calls": [
                {
                    "created_at": c.created_at.isoformat(),
                    "provider": c.provider,
                    "model": c.model,
                    "purpose": c.purpose,
                    "prompt_sha256": c.prompt_sha256,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "cache_read_tokens": c.cache_read_tokens,
                    "cache_write_tokens": c.cache_write_tokens,
                    "cost_usd": str(c.cost_usd) if c.cost_usd is not None else None,
                    "latency_ms": c.latency_ms,
                    "status": c.status,
                    "error": c.error,
                }
                for c in calls
            ],
        }


# -- import ------------------------------------------------------------------------


def import_sample(
    db: Database, store: AudioStore, sample: Sample, data: dict[str, Any], max_bytes: int
) -> str:
    """Create a ready encounter from a sample's audio and its precomputed outputs."""
    with sample.audio_path.open("rb") as audio:
        encounter_id = create_encounter(
            db,
            store,
            title=sample.title,
            source=EncounterSource.SAMPLE,
            audio=audio,
            mime_type="audio/mp4",
            max_bytes=max_bytes,
            sample_slug=sample.slug,
        )
    with db.session() as session:
        encounter = session.get_one(Encounter, encounter_id)
        encounter.status = EncounterStatus.READY
        encounter.transcriber = data["transcriber"]
        encounter.note_model = data["note_model"]
        encounter.check_model = data["check_model"]
        encounter.duration_seconds = data["duration_seconds"]
        _add_children(session, encounter_id, data)
    return encounter_id


def _add_children(session: Session, encounter_id: str, data: dict[str, Any]) -> None:
    session.add_all(
        Segment(
            encounter_id=encounter_id,
            segment_id=s["id"],
            position=index,
            speaker=Speaker(s["speaker"]),
            start_s=s["start"],
            end_s=s["end"],
            text=s["text"],
            redacted_text=s["redacted_text"],
            redaction_spans=s["redaction_spans"],
        )
        for index, s in enumerate(data["segments"])
    )
    session.add_all(PiiEntity(encounter_id=encounter_id, **p) for p in data["pii_entities"])
    session.add_all(
        Statement(
            encounter_id=encounter_id,
            statement_id=s["id"],
            section=Section(s["section"]),
            position=index,
            text=s["text"],
            source_segment_ids=s["source_segment_ids"],
            citations_repaired=s["citations_repaired"],
            support=SupportLabel(s["support"]) if s["support"] else None,
            support_reason=s["support_reason"],
        )
        for index, s in enumerate(data["statements"])
    )
    session.add_all(
        RejectedStatement(
            encounter_id=encounter_id,
            section=Section(r["section"]),
            text=r["text"],
            cited_ids=r["cited_ids"],
            reason=r["reason"],
        )
        for r in data["rejected_statements"]
    )
    session.add_all(
        ModelCall(
            created_at=datetime.fromisoformat(c["created_at"]),
            provider=c["provider"],
            model=c["model"],
            purpose=c["purpose"],
            encounter_id=encounter_id,
            prompt_sha256=c["prompt_sha256"],
            input_tokens=c["input_tokens"],
            output_tokens=c["output_tokens"],
            cache_read_tokens=c["cache_read_tokens"],
            cache_write_tokens=c["cache_write_tokens"],
            cost_usd=Decimal(c["cost_usd"]) if c["cost_usd"] is not None else None,
            latency_ms=c["latency_ms"],
            status=c["status"],
            error=c["error"],
            origin="sample_import",
        )
        for c in data["model_calls"]
    )


def seed_samples(
    db: Database,
    store: AudioStore,
    samples: list[Sample],
    *,
    max_bytes: int,
    outputs: dict[str, dict[str, Any]],
) -> list[str]:
    """Import every sample that is not in the database yet. Returns the new IDs."""
    with db.session() as session:
        present = set(
            session.scalars(
                select(Encounter.sample_slug).where(Encounter.source == EncounterSource.SAMPLE)
            )
        )
    created = []
    for sample in samples:
        if sample.slug in present or sample.slug not in outputs:
            continue
        created.append(import_sample(db, store, sample, outputs[sample.slug], max_bytes))
        log.info("seeded sample %s", sample.slug)
    return created
