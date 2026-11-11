"""Response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from scribe.models import EncounterSource, EncounterStatus, Section, Speaker, SupportLabel

View = Literal["display", "redacted"]


class ConfigOut(BaseModel):
    notice: str
    transcriber_mode: str
    gemini_configured: bool
    anthropic_configured: bool
    gemini_model: str
    soap_model: str
    check_model: str
    retention_days: int
    max_upload_mb: int


class EncounterSummary(BaseModel):
    id: str
    title: str
    source: EncounterSource
    sample_slug: str | None
    status: EncounterStatus
    error: str | None
    created_at: datetime
    duration_seconds: float | None
    audio_available: bool
    audio_deleted_at: datetime | None
    statement_count: int
    flagged_count: int


class RedactionSpanOut(BaseModel):
    start: int
    end: int
    placeholder: str
    entity_type: str


class SegmentOut(BaseModel):
    id: str
    speaker: Speaker
    start: float
    end: float
    text: str
    # Offsets into `text`; only present in the redacted view.
    redactions: list[RedactionSpanOut] = []


class StatementOut(BaseModel):
    id: str
    text: str
    source_segment_ids: list[str]
    citations_repaired: bool
    support: SupportLabel | None
    support_reason: str | None


class SectionOut(BaseModel):
    name: Section
    statements: list[StatementOut]


class RejectedOut(BaseModel):
    section: Section
    text: str
    cited_ids: list[str]
    reason: str


class PiiOut(BaseModel):
    placeholder: str
    entity_type: str
    occurrences: int
    # The real value; only in the display view.
    value: str | None = None


class ModelCallOut(BaseModel):
    id: int
    created_at: datetime
    provider: str
    model: str
    purpose: str
    encounter_id: str | None
    prompt_sha256: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    latency_ms: int
    status: str
    error: str | None
    origin: str


class CheckSummary(BaseModel):
    supported: int = 0
    partially_supported: int = 0
    unsupported: int = 0
    unchecked: int = 0


class EncounterDetail(EncounterSummary):
    view: View
    transcriber: str | None
    note_model: str | None
    check_model: str | None
    audio_mime: str | None
    segments: list[SegmentOut]
    sections: list[SectionOut]
    rejected_statements: list[RejectedOut]
    pii: list[PiiOut]
    check_summary: CheckSummary
    model_calls: list[ModelCallOut]


class AuditTotals(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class AuditPage(BaseModel):
    totals: AuditTotals
    total_items: int
    items: list[ModelCallOut]


class RetentionDeletionOut(BaseModel):
    deleted_at: datetime
    encounter_id: str | None
    audio_path: str
    audio_bytes: int
    age_days: float


class RetentionStatus(BaseModel):
    retention_days: int
    interval_minutes: int
    schedule_enabled: bool
    recent_deletions: list[RetentionDeletionOut]


class RetentionRunOut(BaseModel):
    dry_run: bool
    cutoff: datetime
    deleted: list[RetentionDeletionOut]
