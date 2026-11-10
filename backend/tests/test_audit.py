from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from scribe.audit import AuditLog, prompt_sha256
from scribe.db import Database
from scribe.models import Encounter, EncounterSource, ModelCall
from scribe.pricing import Usage, cost_usd
from tests.conftest import FakeClock


def test_prompt_hash_is_stable_and_order_independent() -> None:
    a = prompt_sha256({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    b = prompt_sha256({"messages": [{"content": "hi", "role": "user"}], "model": "m"})
    assert a == b
    assert len(a) == 64
    assert a != prompt_sha256({"model": "m", "messages": [{"role": "user", "content": "hi!"}]})


def test_cost_uses_price_table() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000)
    # Opus 5.5: $4 / MTok in, $20 / MTok out.
    assert cost_usd("claude-opus-5-5", usage) == Decimal("6.000000")


def test_cost_counts_cache_tokens() -> None:
    usage = Usage(cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    assert cost_usd("claude-opus-5-5", usage) == Decimal("5.200000")


def test_unknown_model_has_no_cost() -> None:
    assert cost_usd("some-unpriced-model", Usage(input_tokens=10)) is None


def test_track_records_successful_call(db: Database, clock: FakeClock) -> None:
    audit = AuditLog(db, clock=clock)
    with audit.track(
        provider="anthropic", model="claude-opus-5-5", purpose="soap_note", prompt={"x": 1}
    ) as call:
        call.usage = Usage(input_tokens=2000, output_tokens=500)

    with db.session() as session:
        row = session.scalars(select(ModelCall)).one()
    assert row.status == "ok"
    assert row.created_at == clock.now
    assert row.prompt_sha256 == prompt_sha256({"x": 1})
    assert (row.input_tokens, row.output_tokens) == (2000, 500)
    assert row.cost_usd == Decimal("0.018000")
    assert row.latency_ms >= 0
    assert row.error is None


def test_track_records_failures_and_reraises(db: Database) -> None:
    audit = AuditLog(db)
    with (
        pytest.raises(RuntimeError, match="boom"),
        audit.track(provider="gemini", model="gemini-x", purpose="transcription", prompt="p"),
    ):
        raise RuntimeError("boom")

    with db.session() as session:
        row = session.scalars(select(ModelCall)).one()
    assert row.status == "error"
    assert row.error == "RuntimeError: boom"
    assert row.cost_usd is None


def test_track_keeps_status_set_by_caller(db: Database) -> None:
    audit = AuditLog(db)
    with (
        pytest.raises(ValueError),
        audit.track(
            provider="anthropic", model="claude-opus-5-5", purpose="soap_note", prompt="p"
        ) as call,
    ):
        call.status = "refusal"
        call.error = "declined"
        raise ValueError("refused")

    with db.session() as session:
        row = session.scalars(select(ModelCall)).one()
    assert (row.status, row.error) == ("refusal", "declined")


def test_track_records_served_model(db: Database) -> None:
    audit = AuditLog(db)
    with audit.track(
        provider="anthropic", model="claude-opus-5-5", purpose="soap_note", prompt="p"
    ) as call:
        call.model = "claude-sonnet-5-5"
    with db.session() as session:
        assert session.scalars(select(ModelCall.model)).one() == "claude-sonnet-5-5"


def test_audit_rows_survive_encounter_deletion(db: Database) -> None:
    with db.session() as session:
        encounter = Encounter(title="t", source=EncounterSource.UPLOAD)
        session.add(encounter)
        session.flush()
        encounter_id = encounter.id
    AuditLog(db).record(
        provider="anthropic",
        model="claude-opus-5-5",
        purpose="soap_note",
        prompt_hash="0" * 64,
        usage=Usage(),
        latency_ms=1,
        status="ok",
        encounter_id=encounter_id,
    )
    with db.session() as session:
        session.delete(session.get_one(Encounter, encounter_id))
    with db.session() as session:
        assert session.scalars(select(ModelCall)).one().encounter_id is None


def test_totals(db: Database) -> None:
    audit = AuditLog(db)
    for tokens in (100, 300):
        audit.record(
            provider="anthropic",
            model="claude-opus-5-5",
            purpose="soap_note",
            prompt_hash="0" * 64,
            usage=Usage(input_tokens=tokens, output_tokens=tokens),
            latency_ms=1,
            status="ok",
        )
    totals = audit.totals()
    assert (totals.calls, totals.input_tokens, totals.output_tokens) == (2, 400, 400)
    assert totals.cost_usd == pytest.approx(400 * 4e-6 + 400 * 20e-6)
