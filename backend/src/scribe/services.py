"""Build the pipeline's services from settings. Shared by the API and the CLI."""

from __future__ import annotations

from scribe.audit import AuditLog
from scribe.config import Settings
from scribe.db import Database
from scribe.llm import ClaudeClient
from scribe.notes.checking import ClaimChecker
from scribe.notes.drafting import NoteDrafter
from scribe.pipeline import Services
from scribe.redaction import Redactor
from scribe.samples import load_samples
from scribe.storage import AudioStore
from scribe.transcription.gemini import GeminiTranscriber
from scribe.transcription.reference import ReferenceTranscriber


def build_services(settings: Settings, db: Database) -> Services:
    audit = AuditLog(db)
    services = Services(
        db=db,
        store=AudioStore(settings.audio_dir),
        audit=audit,
        reference=ReferenceTranscriber(load_samples(settings.samples_dir)),
        redactor_factory=lambda: Redactor(settings.spacy_model),
        transcriber_mode=settings.transcriber,
    )
    if settings.gemini_api_key and settings.has_gemini_key():
        services.gemini = GeminiTranscriber(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_transcribe_model,
            audit=audit,
            timeout_s=settings.gemini_timeout_seconds,
        )
    if settings.anthropic_api_key and settings.has_anthropic_key():
        llm = ClaudeClient(
            audit=audit,
            api_key=settings.anthropic_api_key.get_secret_value(),
            fallbacks=settings.llm_fallbacks,
        )
        services.drafter = NoteDrafter(llm, model=settings.soap_model, effort=settings.soap_effort)
        services.checker = ClaimChecker(
            llm, model=settings.check_model, effort=settings.check_effort
        )
    return services
