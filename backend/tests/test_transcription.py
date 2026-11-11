from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from google.genai import errors, types
from sqlalchemy import select

from scribe.audit import AuditLog
from scribe.config import REPO_ROOT
from scribe.db import Database
from scribe.models import ModelCall, Speaker
from scribe.samples import load_samples
from scribe.schemas import TranscriptSegment
from scribe.transcription import (
    AudioInput,
    NoTranscriberError,
    TranscriptionError,
    normalize_segments,
)
from scribe.transcription.fake import FakeTranscriber
from scribe.transcription.gemini import GeminiTranscriber
from scribe.transcription.reference import ReferenceTranscriber

SAMPLES = load_samples(REPO_ROOT / "samples")


def seg(id_: str, speaker: str, start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(id=id_, speaker=Speaker(speaker), start=start, end=end, text=text)


def test_normalize_keeps_order_cleans_and_renumbers() -> None:
    raw = [
        seg("x1", "doctor", 0.0, 4.0, "What brings you in?"),
        seg("x9", "patient", 5.0, 7.0, "  It hurts   when I swallow. "),
        seg("x2", "doctor", 4.5, 4.6, "   "),
        seg("x3", "doctor", 3.0, 2.0, "Any fever?"),  # out-of-order, inverted time
    ]
    out = normalize_segments(raw)
    assert [(s.id, s.speaker.value, s.text) for s in out] == [
        ("s1", "doctor", "What brings you in?"),
        ("s2", "patient", "It hurts when I swallow."),
        ("s3", "doctor", "Any fever?"),
    ]
    # Times never go backwards, and end is never before start.
    assert (out[2].start, out[2].end) == (5.0, 5.0)


def test_reference_transcriber_matches_slug_or_hash() -> None:
    reference = ReferenceTranscriber(SAMPLES)
    sample = SAMPLES[0]
    by_slug = reference.transcribe(
        AudioInput(path=Path("x"), mime_type="audio/mp4", sample_slug=sample.slug)
    )
    by_hash = reference.transcribe(
        AudioInput(path=Path("x"), mime_type="audio/mp4", sha256=sample.transcript.audio_sha256)
    )
    assert by_slug.segments == by_hash.segments == sample.transcript.segments
    assert by_slug.provider == "reference"


def test_reference_transcriber_rejects_unknown_audio() -> None:
    with pytest.raises(NoTranscriberError):
        ReferenceTranscriber(SAMPLES).transcribe(
            AudioInput(path=Path("x"), mime_type="audio/wav", sha256="0" * 64)
        )


def test_fake_transcriber_records_calls() -> None:
    fake = FakeTranscriber([seg("a", "doctor", 0, 1, "Hello.")])
    audio = AudioInput(path=Path("x"), mime_type="audio/wav")
    result = fake.transcribe(audio)
    assert fake.calls == [audio]
    assert result.label == "fake:fake-1"


# --- Gemini, with a stub client -------------------------------------------------------


def _response(payload: dict[str, Any]) -> types.GenerateContentResponse:
    return types.GenerateContentResponse.model_validate(
        {
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": json.dumps(payload)}]}}
            ],
            "usage_metadata": {
                "prompt_token_count": 5000,
                "candidates_token_count": 800,
                "thoughts_token_count": 200,
            },
        }
    )


def _rate_limited() -> errors.ClientError:
    return errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "quota",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}
                ],
            }
        },
    )


class _Models:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.requests: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, types.GenerateContentResponse)
        return outcome


class _Client:
    def __init__(self, outcomes: list[Any]) -> None:
        self.models = _Models(outcomes)


GOOD = {
    "segments": [
        {"id": "1", "speaker": "doctor", "start": 0.4, "end": 3.1, "text": "How are you?"},
        {"id": "2", "speaker": "patient", "start": 3.6, "end": 5.0, "text": "Sore throat."},
    ]
}


def _transcriber(
    db: Database, outcomes: list[Any], sleeps: list[float]
) -> tuple[GeminiTranscriber, _Client]:
    client = _Client(outcomes)
    transcriber = GeminiTranscriber(
        api_key="test",
        model="gemini-3.5-flash-lite",
        audit=AuditLog(db),
        client=client,  # type: ignore[arg-type]
        sleep=sleeps.append,
    )
    return transcriber, client


def _audio(tmp_path: Path) -> AudioInput:
    path = tmp_path / "a.m4a"
    path.write_bytes(b"audio")
    return AudioInput(path=path, mime_type="audio/x-m4a", sha256="ab" * 32)


def test_gemini_parses_segments_and_audits(db: Database, tmp_path: Path) -> None:
    sleeps: list[float] = []
    transcriber, client = _transcriber(db, [_response(GOOD)], sleeps)

    result = transcriber.transcribe(_audio(tmp_path), encounter_id=None)

    assert [(s.id, s.speaker.value, s.text) for s in result.segments] == [
        ("s1", "doctor", "How are you?"),
        ("s2", "patient", "Sore throat."),
    ]
    assert result.label == "gemini:gemini-3.5-flash-lite"
    contents = client.models.requests[0]["contents"]
    assert contents[0].inline_data.mime_type == "audio/mp4"
    with db.session() as session:
        call = session.scalars(select(ModelCall)).one()
    assert (call.provider, call.purpose, call.status) == ("gemini", "transcription", "ok")
    assert (call.input_tokens, call.output_tokens) == (5000, 1000)
    assert sleeps == []


def test_gemini_backs_off_on_429_and_logs_each_attempt(db: Database, tmp_path: Path) -> None:
    sleeps: list[float] = []
    transcriber, _ = _transcriber(db, [_rate_limited(), _response(GOOD)], sleeps)

    result = transcriber.transcribe(_audio(tmp_path))

    assert len(result.segments) == 2
    assert sleeps == [8.0]  # server asked for 7 s; one second of slack
    with db.session() as session:
        statuses = session.scalars(select(ModelCall.status).order_by(ModelCall.id)).all()
    assert statuses == ["rate_limited", "ok"]


def test_gemini_gives_up_after_max_retries(db: Database, tmp_path: Path) -> None:
    sleeps: list[float] = []
    transcriber, _ = _transcriber(db, [_rate_limited() for _ in range(6)], sleeps)
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(_audio(tmp_path))
    assert len(sleeps) == 5


def test_gemini_does_not_retry_client_errors(db: Database, tmp_path: Path) -> None:
    bad_request = errors.ClientError(400, {"error": {"code": 400, "message": "bad"}})
    sleeps: list[float] = []
    transcriber, _ = _transcriber(db, [bad_request], sleeps)
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(_audio(tmp_path))
    assert sleeps == []


@pytest.mark.parametrize(
    "payload",
    [{"segments": []}, {"segments": [{"speaker": "nurse", "start": 0, "end": 1, "text": "x"}]}],
)
def test_gemini_rejects_bad_output(db: Database, tmp_path: Path, payload: dict[str, Any]) -> None:
    transcriber, _ = _transcriber(db, [_response(payload)], [])
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(_audio(tmp_path))
