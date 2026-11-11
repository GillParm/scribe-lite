"""Smoke tests against the real APIs.

Skipped unless RUN_LIVE_TESTS=1 and the matching key is set, so a plain `pytest` never
spends money even on a machine with keys in .env:

    RUN_LIVE_TESTS=1 uv run pytest -m live
"""

from __future__ import annotations

import os

import pytest

from scribe.audit import AuditLog
from scribe.config import REPO_ROOT, get_settings
from scribe.db import Database
from scribe.llm import ClaudeClient
from scribe.models import Speaker
from scribe.notes.drafting import NoteDrafter, PromptSegment
from scribe.samples import load_samples
from scribe.transcription import AudioInput
from scribe.transcription.gemini import GeminiTranscriber

pytestmark = pytest.mark.live
settings = get_settings()
opted_in = os.environ.get("RUN_LIVE_TESTS") == "1"


@pytest.mark.skipif(
    not (opted_in and settings.has_gemini_key()), reason="needs RUN_LIVE_TESTS=1 and GEMINI_API_KEY"
)
def test_gemini_transcribes_a_sample(db: Database) -> None:
    assert settings.gemini_api_key is not None
    sample = next(s for s in load_samples(REPO_ROOT / "samples") if s.slug == "sore-throat")
    # flash-lite keeps the smoke test fast; the default model is compared in eval/.
    transcriber = GeminiTranscriber(
        api_key=settings.gemini_api_key.get_secret_value(),
        model="gemini-3.5-flash-lite",
        audit=AuditLog(db),
    )
    result = transcriber.transcribe(AudioInput(path=sample.audio_path, mime_type="audio/mp4"))
    assert len(result.segments) > 10
    assert {s.speaker for s in result.segments} == {Speaker.DOCTOR, Speaker.PATIENT}
    assert "strep" in " ".join(s.text for s in result.segments).lower()


@pytest.mark.skipif(
    not (opted_in and settings.has_anthropic_key()),
    reason="needs RUN_LIVE_TESTS=1 and ANTHROPIC_API_KEY",
)
def test_claude_drafts_a_sourced_note(db: Database) -> None:
    assert settings.anthropic_api_key is not None
    llm = ClaudeClient(audit=AuditLog(db), api_key=settings.anthropic_api_key.get_secret_value())
    drafter = NoteDrafter(llm, model=settings.soap_model, effort="low")
    segments = [
        PromptSegment("s1", "doctor", "Hi [PERSON_1], what brings you in?"),
        PromptSegment("s2", "patient", "I've had a dry cough for five days. No fever."),
        PromptSegment("s3", "doctor", "Your lungs are clear. It's a viral cough; try honey."),
    ]
    note = drafter.draft(segments)
    statements = list(note.statements())
    assert statements
    assert all(set(s.source_segment_ids) <= {"s1", "s2", "s3"} for s in statements)
    assert AuditLog(db).totals().calls == 1
