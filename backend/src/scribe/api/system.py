"""Health, configuration, audit log and retention endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from scribe.api.schemas import (
    AuditPage,
    AuditTotals,
    ConfigOut,
    ModelCallOut,
    RetentionDeletionOut,
    RetentionRunOut,
    RetentionStatus,
)
from scribe.api.state import DEMO_NOTICE, AppState, get_state
from scribe.audit import AuditLog
from scribe.clock import utcnow
from scribe.models import ModelCall, RetentionDeletion
from scribe.retention import purge_expired_audio

router = APIRouter(prefix="/api")
State = Annotated[AppState, Depends(get_state)]


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/config", response_model=ConfigOut, tags=["system"])
def config(state: State) -> ConfigOut:
    settings = state.settings
    return ConfigOut(
        notice=DEMO_NOTICE,
        transcriber_mode=settings.transcriber,
        gemini_configured=state.services.gemini is not None,
        anthropic_configured=state.services.drafter is not None,
        gemini_model=settings.gemini_transcribe_model,
        soap_model=settings.soap_model,
        check_model=settings.check_model,
        retention_days=settings.retention_days,
        max_upload_mb=settings.max_upload_mb,
    )


@router.get("/audit", response_model=AuditPage, tags=["audit"])
def audit_log(
    state: State,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    encounter_id: str | None = None,
) -> AuditPage:
    query = select(ModelCall)
    if encounter_id:
        query = query.where(ModelCall.encounter_id == encounter_id)
    with state.db.session() as session:
        total_items = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = session.scalars(
            query.order_by(ModelCall.created_at.desc(), ModelCall.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        items = [ModelCallOut.model_validate(r, from_attributes=True) for r in rows]
    totals = AuditLog(state.db).totals()
    return AuditPage(
        totals=AuditTotals(
            calls=totals.calls,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cost_usd=totals.cost_usd,
        ),
        total_items=total_items,
        items=items,
    )


@router.get("/retention", response_model=RetentionStatus, tags=["retention"])
def retention_status(state: State) -> RetentionStatus:
    with state.db.session() as session:
        recent = session.scalars(
            select(RetentionDeletion).order_by(RetentionDeletion.deleted_at.desc()).limit(20)
        ).all()
        deletions = [RetentionDeletionOut.model_validate(r, from_attributes=True) for r in recent]
    return RetentionStatus(
        retention_days=state.settings.retention_days,
        interval_minutes=state.settings.retention_interval_minutes,
        schedule_enabled=state.settings.retention_schedule_enabled,
        recent_deletions=deletions,
    )


@router.post("/retention/run", response_model=RetentionRunOut, tags=["retention"])
def run_retention(state: State, dry_run: bool = True) -> RetentionRunOut:
    report = purge_expired_audio(
        state.db,
        state.services.store,
        retention_days=state.settings.retention_days,
        now=utcnow(),
        dry_run=dry_run,
    )
    return RetentionRunOut(
        dry_run=report.dry_run,
        cutoff=report.cutoff,
        deleted=[
            RetentionDeletionOut(
                deleted_at=report.ran_at,
                encounter_id=d.encounter_id,
                audio_path=d.audio_path,
                audio_bytes=d.audio_bytes,
                age_days=d.age_days,
            )
            for d in report.deleted
        ],
    )
