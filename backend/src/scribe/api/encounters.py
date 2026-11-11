"""Encounter endpoints: list, upload, detail, reprocess, delete, audio."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from scribe.api.schemas import (
    CheckSummary,
    EncounterDetail,
    EncounterSummary,
    ModelCallOut,
    PiiOut,
    RedactionSpanOut,
    RejectedOut,
    SectionOut,
    SegmentOut,
    StatementOut,
    View,
)
from scribe.api.state import AppState, get_state
from scribe.encounters import create_encounter
from scribe.models import (
    Encounter,
    EncounterSource,
    EncounterStatus,
    ModelCall,
    SupportLabel,
)
from scribe.notes.schemas import SECTION_ORDER
from scribe.redaction import Vault, VaultEntry
from scribe.storage import AUDIO_TYPES, AudioTooLargeError

router = APIRouter(prefix="/api/encounters", tags=["encounters"])
State = Annotated[AppState, Depends(get_state)]

IN_PROGRESS = {
    EncounterStatus.UPLOADED,
    EncounterStatus.TRANSCRIBING,
    EncounterStatus.REDACTING,
    EncounterStatus.DRAFTING,
    EncounterStatus.CHECKING,
}
FLAGGED = {SupportLabel.UNSUPPORTED, SupportLabel.PARTIALLY_SUPPORTED}
EXTENSION_TYPES = {ext: mime for mime, ext in reversed(list(AUDIO_TYPES.items()))}


def _summary(encounter: Encounter) -> EncounterSummary:
    return EncounterSummary(
        id=encounter.id,
        title=encounter.title,
        source=encounter.source,
        sample_slug=encounter.sample_slug,
        status=encounter.status,
        error=encounter.error,
        created_at=encounter.created_at,
        duration_seconds=encounter.duration_seconds,
        audio_available=encounter.audio_path is not None,
        audio_deleted_at=encounter.audio_deleted_at,
        statement_count=len(encounter.statements),
        flagged_count=sum(1 for s in encounter.statements if s.support in FLAGGED),
    )


def _load(session: Session, encounter_id: str) -> Encounter:
    encounter = session.get(
        Encounter,
        encounter_id,
        options=[
            selectinload(Encounter.segments),
            selectinload(Encounter.statements),
            selectinload(Encounter.pii_entities),
            selectinload(Encounter.rejected_statements),
        ],
    )
    if encounter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "encounter not found")
    return encounter


@router.get("", response_model=list[EncounterSummary])
def list_encounters(state: State) -> list[EncounterSummary]:
    with state.db.session() as session:
        encounters = session.scalars(
            select(Encounter)
            .options(selectinload(Encounter.statements))
            .order_by(Encounter.created_at.desc())
        ).all()
        return [_summary(e) for e in encounters]


@router.post("", response_model=EncounterSummary, status_code=status.HTTP_202_ACCEPTED)
def upload_encounter(
    state: State,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File(description="Audio of a synthetic conversation")],
    title: Annotated[str | None, Form(max_length=200)] = None,
    source: Annotated[EncounterSource, Form()] = EncounterSource.UPLOAD,
) -> EncounterSummary:
    if source == EncounterSource.SAMPLE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "source must be upload or recording"
        )
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in AUDIO_TYPES:
        suffix = "." + (file.filename or "").rsplit(".", 1)[-1].lower()
        mime = EXTENSION_TYPES.get(suffix, mime)
    if mime not in AUDIO_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"unsupported audio type {file.content_type!r}; use wav, mp3, m4a, ogg, flac or webm",
        )
    default_title = "Browser recording" if source == EncounterSource.RECORDING else "Uploaded audio"
    try:
        encounter_id = create_encounter(
            state.db,
            state.services.store,
            title=(title or "").strip() or file.filename or default_title,
            source=source,
            audio=file.file,
            mime_type=mime,
            max_bytes=state.settings.max_upload_mb * 1024 * 1024,
        )
    except AudioTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
    background.add_task(state.pipeline.process, encounter_id)
    with state.db.session() as session:
        return _summary(_load(session, encounter_id))


@router.get("/{encounter_id}", response_model=EncounterDetail)
def get_encounter(
    encounter_id: str, state: State, view: Annotated[View, Query()] = "display"
) -> EncounterDetail:
    with state.db.session() as session:
        encounter = _load(session, encounter_id)
        calls = session.scalars(
            select(ModelCall)
            .where(ModelCall.encounter_id == encounter_id)
            .order_by(ModelCall.created_at, ModelCall.id)
        ).all()
        vault = Vault(
            {
                p.placeholder: VaultEntry(p.placeholder, p.entity_type, p.value, p.variants)
                for p in encounter.pii_entities
            }
        )
        display = view == "display"

        def shown(text: str | None) -> str | None:
            # Model output is stored redacted; real values go back in only for display.
            return vault.reveal(text) if (display and text is not None) else text

        segments = [
            SegmentOut(
                id=s.segment_id,
                speaker=s.speaker,
                start=s.start_s,
                end=s.end_s,
                text=s.text if display else (s.redacted_text or s.text),
                redactions=(
                    []
                    if display
                    else [RedactionSpanOut.model_validate(r) for r in s.redaction_spans or []]
                ),
            )
            for s in encounter.segments
        ]
        sections = [
            SectionOut(
                name=name,
                statements=[
                    StatementOut(
                        id=s.statement_id,
                        text=shown(s.text) or "",
                        source_segment_ids=s.source_segment_ids,
                        citations_repaired=s.citations_repaired,
                        support=s.support,
                        support_reason=shown(s.support_reason),
                    )
                    for s in encounter.statements
                    if s.section == name
                ],
            )
            for name in SECTION_ORDER
        ]
        counts = CheckSummary()
        for statement in encounter.statements:
            if statement.support is not None:
                setattr(
                    counts,
                    statement.support.value,
                    getattr(counts, statement.support.value) + 1,
                )
        return EncounterDetail(
            **_summary(encounter).model_dump(),
            view=view,
            transcriber=encounter.transcriber,
            note_model=encounter.note_model,
            check_model=encounter.check_model,
            audio_mime=encounter.audio_mime,
            segments=segments,
            sections=sections,
            rejected_statements=[
                RejectedOut(
                    section=r.section,
                    text=shown(r.text) or "",
                    cited_ids=r.cited_ids,
                    reason=r.reason,
                )
                for r in encounter.rejected_statements
            ],
            pii=[
                PiiOut(
                    placeholder=p.placeholder,
                    entity_type=p.entity_type,
                    occurrences=p.occurrences,
                    value=p.value if display else None,
                )
                for p in encounter.pii_entities
            ],
            check_summary=counts,
            model_calls=[ModelCallOut.model_validate(c, from_attributes=True) for c in calls],
        )


@router.post(
    "/{encounter_id}/process",
    response_model=EncounterSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_encounter(
    encounter_id: str, state: State, background: BackgroundTasks
) -> EncounterSummary:
    with state.db.session() as session:
        encounter = _load(session, encounter_id)
        if encounter.status in IN_PROGRESS - {EncounterStatus.UPLOADED}:
            raise HTTPException(status.HTTP_409_CONFLICT, "this encounter is being processed")
        if encounter.audio_path is None:
            raise HTTPException(status.HTTP_410_GONE, "audio was deleted by the retention job")
        encounter.status = EncounterStatus.UPLOADED
        encounter.error = None
        summary = _summary(encounter)
    background.add_task(state.pipeline.process, encounter_id)
    return summary


@router.delete("/{encounter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_encounter(encounter_id: str, state: State) -> Response:
    with state.db.session() as session:
        encounter = _load(session, encounter_id)
        if encounter.audio_path:
            state.services.store.delete(encounter.audio_path)
        session.delete(encounter)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{encounter_id}/audio", response_class=FileResponse)
def get_audio(encounter_id: str, state: State) -> FileResponse:
    with state.db.session() as session:
        encounter = session.get(Encounter, encounter_id)
        if encounter is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "encounter not found")
        if encounter.audio_path is None:
            raise HTTPException(status.HTTP_410_GONE, "audio was deleted by the retention job")
        path = state.services.store.path(encounter.audio_path)
        mime = encounter.audio_mime or "application/octet-stream"
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "audio file is missing")
    # FileResponse answers Range requests, which the browser needs to seek.
    return FileResponse(path, media_type=mime)
