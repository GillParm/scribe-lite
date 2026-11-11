"""ORM models.

Everything here stays on the local database: original transcript text, the PII
placeholder mapping and the audit log. Only redacted text is ever sent to a language
model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from scribe.clock import utcnow
from scribe.db import Base, UTCDateTime


class EncounterStatus(StrEnum):
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    REDACTING = "redacting"
    DRAFTING = "drafting"
    CHECKING = "checking"
    READY = "ready"
    FAILED = "failed"


class EncounterSource(StrEnum):
    UPLOAD = "upload"
    RECORDING = "recording"
    SAMPLE = "sample"


class Speaker(StrEnum):
    DOCTOR = "doctor"
    PATIENT = "patient"


class Section(StrEnum):
    SUBJECTIVE = "subjective"
    OBJECTIVE = "objective"
    ASSESSMENT = "assessment"
    PLAN = "plan"


class SupportLabel(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    UNCHECKED = "unchecked"


def _enum(cls: type[StrEnum], length: int = 24) -> Enum:
    # Stored as VARCHAR so adding a value never needs a Postgres enum migration.
    return Enum(
        cls,
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda members: [m.value for m in members],
    )


def _uuid() -> str:
    return str(uuid.uuid4())


class Encounter(Base):
    __tablename__ = "encounters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[EncounterSource] = mapped_column(_enum(EncounterSource))
    sample_slug: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[EncounterStatus] = mapped_column(
        _enum(EncounterStatus), default=EncounterStatus.UPLOADED
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    # Audio lives on disk (or a bucket); the database only keeps where it is.
    audio_path: Mapped[str | None] = mapped_column(String(500))
    audio_mime: Mapped[str | None] = mapped_column(String(100))
    audio_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    audio_bytes: Mapped[int | None] = mapped_column(Integer)
    audio_deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    transcriber: Mapped[str | None] = mapped_column(String(100))
    note_model: Mapped[str | None] = mapped_column(String(100))
    check_model: Mapped[str | None] = mapped_column(String(100))

    segments: Mapped[list[Segment]] = relationship(
        back_populates="encounter",
        cascade="all, delete-orphan",
        order_by="Segment.position",
    )
    statements: Mapped[list[Statement]] = relationship(
        back_populates="encounter",
        cascade="all, delete-orphan",
        order_by="Statement.position",
    )
    pii_entities: Mapped[list[PiiEntity]] = relationship(
        back_populates="encounter",
        cascade="all, delete-orphan",
        order_by="PiiEntity.placeholder",
    )
    rejected_statements: Mapped[list[RejectedStatement]] = relationship(
        back_populates="encounter", cascade="all, delete-orphan"
    )


class Segment(Base):
    """One speaker turn (or part of one) in the transcript."""

    __tablename__ = "segments"
    __table_args__ = (UniqueConstraint("encounter_id", "segment_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    encounter_id: Mapped[str] = mapped_column(
        ForeignKey("encounters.id", ondelete="CASCADE"), index=True
    )
    segment_id: Mapped[str] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[Speaker] = mapped_column(_enum(Speaker, 16))
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    redacted_text: Mapped[str | None] = mapped_column(Text)
    # [{"start": int, "end": int, "placeholder": str, "entity_type": str}], offsets into
    # redacted_text. Lets the UI mark exactly what was hidden from the model.
    redaction_spans: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    encounter: Mapped[Encounter] = relationship(back_populates="segments")


class PiiEntity(Base):
    """Placeholder -> real value. Never leaves this database."""

    __tablename__ = "pii_entities"
    __table_args__ = (UniqueConstraint("encounter_id", "placeholder"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    encounter_id: Mapped[str] = mapped_column(
        ForeignKey("encounters.id", ondelete="CASCADE"), index=True
    )
    placeholder: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(Text)
    variants: Mapped[list[str]] = mapped_column(JSON, default=list)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)

    encounter: Mapped[Encounter] = relationship(back_populates="pii_entities")


class Statement(Base):
    """One sentence of the SOAP note, with the transcript segments it cites."""

    __tablename__ = "statements"
    __table_args__ = (UniqueConstraint("encounter_id", "statement_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    encounter_id: Mapped[str] = mapped_column(
        ForeignKey("encounters.id", ondelete="CASCADE"), index=True
    )
    statement_id: Mapped[str] = mapped_column(String(32))
    section: Mapped[Section] = mapped_column(_enum(Section, 16))
    position: Mapped[int] = mapped_column(Integer)
    # Redacted text as the model wrote it; placeholders are restored only for display.
    text: Mapped[str] = mapped_column(Text)
    source_segment_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    citations_repaired: Mapped[bool] = mapped_column(default=False)
    support: Mapped[SupportLabel | None] = mapped_column(_enum(SupportLabel, 24))
    support_reason: Mapped[str | None] = mapped_column(Text)

    encounter: Mapped[Encounter] = relationship(back_populates="statements")


class RejectedStatement(Base):
    """A drafted statement dropped because none of its citations pointed at a real segment."""

    __tablename__ = "rejected_statements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    encounter_id: Mapped[str] = mapped_column(
        ForeignKey("encounters.id", ondelete="CASCADE"), index=True
    )
    section: Mapped[Section] = mapped_column(_enum(Section, 16))
    text: Mapped[str] = mapped_column(Text)
    cited_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text)

    encounter: Mapped[Encounter] = relationship(back_populates="rejected_statements")


class ModelCall(Base):
    """Audit log: one row per call to a hosted model, successful or not."""

    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(40))
    # Kept when the encounter is deleted so the audit trail survives.
    encounter_id: Mapped[str | None] = mapped_column(
        ForeignKey("encounters.id", ondelete="SET NULL"), index=True
    )
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # NULL when the model has no entry in the price table.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    # "live" for calls made by this deployment; "sample_import" for the recorded calls
    # that produced the bundled sample outputs, imported with them.
    origin: Mapped[str] = mapped_column(String(20), default="live", server_default="live")


class RetentionDeletion(Base):
    """What the retention job deleted, and when."""

    __tablename__ = "retention_deletions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deleted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    encounter_id: Mapped[str | None] = mapped_column(
        ForeignKey("encounters.id", ondelete="SET NULL"), index=True
    )
    audio_path: Mapped[str] = mapped_column(String(500))
    audio_bytes: Mapped[int] = mapped_column(Integer, default=0)
    age_days: Mapped[float] = mapped_column(Float)
    retention_days: Mapped[int] = mapped_column(Integer)
