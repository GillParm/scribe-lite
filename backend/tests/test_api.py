from __future__ import annotations

import io
import wave
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from scribe.api.main import create_app
from scribe.audit import AuditLog
from scribe.clock import utcnow
from scribe.config import REPO_ROOT, Settings
from scribe.db import Database
from scribe.llm import ClaudeClient
from scribe.models import Encounter, EncounterStatus, ModelCall, Speaker
from scribe.notes.checking import CheckResult, ClaimChecker, Verdict
from scribe.notes.drafting import NoteDrafter
from scribe.notes.schemas import DraftNote, DraftStatement
from scribe.pipeline import Services
from scribe.redaction import Redactor
from scribe.samples import load_samples
from scribe.schemas import TranscriptSegment
from scribe.storage import AudioStore
from scribe.transcription.fake import FakeTranscriber
from scribe.transcription.reference import ReferenceTranscriber
from tests.fakes import FakeAnthropic, parsed_response

FAKE_SEGMENTS = [
    TranscriptSegment(id="a", speaker=Speaker.DOCTOR, start=0, end=2, text="Hi Priya, any cough?"),
    TranscriptSegment(id="b", speaker=Speaker.PATIENT, start=2, end=4, text="No cough at all."),
]
DRAFT = DraftNote(
    subjective=[DraftStatement(text="[PERSON_1] denies cough.", source_segment_ids=["s2"])],
    objective=[],
    assessment=[],
    plan=[],
)
VERDICTS = CheckResult(
    verdicts=[Verdict(statement_id="subjective-1", label="supported", reason="Stated in s2.")]
)


def wav_bytes(seconds: float = 0.1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buffer.getvalue()


@pytest.fixture
def app_parts(
    db: Database, tmp_path: Path, redactor: Redactor
) -> tuple[Settings, Services, FakeAnthropic]:
    settings = Settings(
        database_url=db.url,
        data_dir=tmp_path / "data",
        samples_dir=REPO_ROOT / "samples",
        retention_schedule_enabled=False,
        transcriber="auto",
        max_upload_mb=1,
    )
    fake_llm = FakeAnthropic([parsed_response(DRAFT), parsed_response(VERDICTS)])
    audit = AuditLog(db)
    llm = ClaudeClient(audit=audit, client=fake_llm)  # type: ignore[arg-type]
    services = Services(
        db=db,
        store=AudioStore(settings.audio_dir),
        audit=audit,
        reference=ReferenceTranscriber(load_samples(settings.samples_dir)),
        redactor_factory=lambda: redactor,
        gemini=FakeTranscriber(FAKE_SEGMENTS),  # stands in for Gemini on non-sample audio
        drafter=NoteDrafter(llm, model="claude-opus-5-5", effort="medium"),
        checker=ClaimChecker(llm, model="claude-opus-5-5", effort="medium"),
    )
    return settings, services, fake_llm


@pytest.fixture
def client(app_parts: tuple[Settings, Services, FakeAnthropic]) -> Iterator[TestClient]:
    settings, services, _ = app_parts
    with TestClient(create_app(settings, services)) as test_client:
        yield test_client


def sample_id(client: TestClient, slug: str) -> str:
    encounters = client.get("/api/encounters").json()
    return str(next(e["id"] for e in encounters if e["sample_slug"] == slug))


def test_health_and_config(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}
    config = client.get("/api/config").json()
    assert config["notice"].startswith("Demo only. Not for clinical use.")
    assert config["anthropic_configured"] is True
    assert "key" not in str(config).lower()


def test_openapi_description_carries_the_disclaimer(client: TestClient) -> None:
    info = client.get("/openapi.json").json()["info"]
    assert "not for clinical use" in info["title"].lower()
    assert "Demo only. Not for clinical use." in info["description"]


def test_samples_are_seeded_once(
    client: TestClient, app_parts: tuple[Settings, Services, FakeAnthropic]
) -> None:
    encounters = client.get("/api/encounters").json()
    assert sorted(e["sample_slug"] for e in encounters) == [
        "ankle-sprain",
        "diabetes-follow-up",
        "sore-throat",
    ]
    assert all(e["status"] == "ready" and e["statement_count"] > 0 for e in encounters)
    settings, services, _ = app_parts
    with TestClient(create_app(settings, services)) as again:
        assert len(again.get("/api/encounters").json()) == 3


def test_display_view_restores_values(client: TestClient) -> None:
    detail = client.get(f"/api/encounters/{sample_id(client, 'sore-throat')}").json()
    assert detail["view"] == "display"
    assert "Maria Alvarez" in detail["segments"][1]["text"]
    assert detail["segments"][1]["redactions"] == []
    assert [s["name"] for s in detail["sections"]] == [
        "subjective",
        "objective",
        "assessment",
        "plan",
    ]
    assert {p["entity_type"] for p in detail["pii"]} >= {"PERSON", "PHONE_NUMBER"}
    assert all(p["value"] for p in detail["pii"])
    assert detail["check_summary"]["supported"] > 0
    assert {c["origin"] for c in detail["model_calls"]} == {"sample_import"}


def test_redacted_view_shows_what_the_model_saw(client: TestClient) -> None:
    encounter_id = sample_id(client, "sore-throat")
    detail = client.get(f"/api/encounters/{encounter_id}?view=redacted").json()
    segment = detail["segments"][1]
    assert "Maria" not in segment["text"]
    for span in segment["redactions"]:
        assert segment["text"][span["start"] : span["end"]] == span["placeholder"]
    assert all(p["value"] is None for p in detail["pii"])
    body = str(detail)
    assert "416-555-0182" not in body and "Alvarez" not in body


def test_unknown_encounter_is_404(client: TestClient) -> None:
    assert client.get("/api/encounters/nope").status_code == 404
    assert client.get("/api/encounters/nope/audio").status_code == 404


def test_audio_supports_range_requests(client: TestClient) -> None:
    encounter_id = sample_id(client, "ankle-sprain")
    full = client.get(f"/api/encounters/{encounter_id}/audio")
    assert full.status_code == 200
    assert full.headers["content-type"] == "audio/mp4"
    partial = client.get(f"/api/encounters/{encounter_id}/audio", headers={"Range": "bytes=0-99"})
    assert partial.status_code == 206
    assert len(partial.content) == 100


def test_upload_is_processed_in_the_background(client: TestClient) -> None:
    response = client.post(
        "/api/encounters",
        files={"file": ("visit.wav", wav_bytes(), "audio/wav")},
        data={"title": "Cough check", "source": "recording"},
    )
    assert response.status_code == 202
    created = response.json()
    assert (created["title"], created["source"]) == ("Cough check", "recording")

    detail = client.get(f"/api/encounters/{created['id']}").json()
    assert detail["status"] == "ready", detail["error"]
    assert detail["transcriber"] == "fake:fake-1"
    statement = detail["sections"][0]["statements"][0]
    assert statement["text"] == "Priya denies cough."
    assert statement["support"] == "supported"
    redacted = client.get(f"/api/encounters/{created['id']}?view=redacted").json()
    assert redacted["sections"][0]["statements"][0]["text"] == "[PERSON_1] denies cough."


def test_upload_type_is_inferred_from_the_extension(client: TestClient) -> None:
    response = client.post(
        "/api/encounters",
        files={"file": ("visit.wav", wav_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 202


def test_upload_rejects_non_audio_and_oversized_files(client: TestClient) -> None:
    bad_type = client.post("/api/encounters", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert bad_type.status_code == 415
    too_big = client.post(
        "/api/encounters",
        files={"file": ("long.wav", wav_bytes(40), "audio/wav")},  # ~1.3 MB, limit 1 MB
    )
    assert too_big.status_code == 413
    sample_source = client.post(
        "/api/encounters",
        files={"file": ("a.wav", wav_bytes(), "audio/wav")},
        data={"source": "sample"},
    )
    assert sample_source.status_code == 422


def test_reprocess_conflicts_while_running(client: TestClient, db: Database) -> None:
    encounter_id = sample_id(client, "ankle-sprain")
    with db.session() as session:
        session.get_one(Encounter, encounter_id).status = EncounterStatus.CHECKING
    assert client.post(f"/api/encounters/{encounter_id}/process").status_code == 409


def test_delete_removes_audio_but_keeps_audit_rows(client: TestClient, db: Database) -> None:
    encounter_id = sample_id(client, "diabetes-follow-up")
    with db.session() as session:
        audio_path = session.get_one(Encounter, encounter_id).audio_path
    assert audio_path
    assert client.delete(f"/api/encounters/{encounter_id}").status_code == 204
    assert client.get(f"/api/encounters/{encounter_id}").status_code == 404
    with db.session() as session:
        orphaned = session.scalars(select(ModelCall).where(ModelCall.encounter_id.is_(None))).all()
    assert len(orphaned) == 2


def test_audit_endpoint(client: TestClient) -> None:
    page = client.get("/api/audit?limit=2").json()
    assert page["total_items"] == 6
    assert len(page["items"]) == 2
    assert page["totals"]["calls"] == 6
    assert page["totals"]["cost_usd"] > 0
    item = page["items"][0]
    assert set(item) >= {
        "created_at",
        "provider",
        "model",
        "purpose",
        "prompt_sha256",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
    }
    one = sample_id(client, "sore-throat")
    assert client.get(f"/api/audit?encounter_id={one}").json()["total_items"] == 2


def test_retention_run_and_status(client: TestClient, db: Database) -> None:
    encounter_id = sample_id(client, "sore-throat")
    assert client.post("/api/retention/run?dry_run=true").json()["deleted"] == []
    with db.session() as session:
        session.get_one(Encounter, encounter_id).created_at = utcnow() - timedelta(days=45)

    preview = client.post("/api/retention/run?dry_run=true").json()
    assert [d["encounter_id"] for d in preview["deleted"]] == [encounter_id]
    assert client.get(f"/api/encounters/{encounter_id}/audio").status_code == 200

    done = client.post("/api/retention/run?dry_run=false").json()
    assert done["dry_run"] is False
    assert client.get(f"/api/encounters/{encounter_id}/audio").status_code == 410
    summary = client.get(f"/api/encounters/{encounter_id}").json()
    assert summary["audio_available"] is False
    assert summary["statement_count"] > 0  # the note stays
    status = client.get("/api/retention").json()
    assert status["retention_days"] == 30
    assert [d["encounter_id"] for d in status["recent_deletions"]] == [encounter_id]
    assert client.post(f"/api/encounters/{encounter_id}/process").status_code == 410
