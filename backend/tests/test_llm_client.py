from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from scribe.audit import AuditLog
from scribe.db import Database
from scribe.llm import ClaudeClient, LLMOutputError, ModelRefusalError
from scribe.models import ModelCall
from tests.fakes import FakeAnthropic, parsed_response


class Answer(BaseModel):
    value: str


def _client_and_fake(
    db: Database, outcomes: list[object], fallbacks: bool = True
) -> tuple[ClaudeClient, FakeAnthropic]:
    fake = FakeAnthropic(outcomes)
    client = ClaudeClient(
        audit=AuditLog(db),
        fallbacks=fallbacks,
        client=fake,  # type: ignore[arg-type]
    )
    return client, fake


def _client(db: Database, outcomes: list[object], fallbacks: bool = True) -> ClaudeClient:
    return _client_and_fake(db, outcomes, fallbacks)[0]


def _call(client: ClaudeClient) -> Answer:
    return client.parse(
        purpose="test",
        model="claude-opus-5-5",
        effort="low",
        system="sys",
        user="question",
        output_model=Answer,
    )


def _rows(db: Database) -> list[ModelCall]:
    with db.session() as session:
        return list(session.scalars(select(ModelCall).order_by(ModelCall.id)))


def test_parse_returns_model_and_audits(db: Database) -> None:
    client, fake = _client_and_fake(db, [parsed_response(Answer(value="ok"))])
    assert _call(client) == Answer(value="ok")

    request = fake.calls[0]
    assert request["output_format"] is Answer
    assert request["output_config"] == {"effort": "low"}
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in request  # Opus 5.5 always thinks; effort is the control

    (row,) = _rows(db)
    assert (row.provider, row.model, row.purpose, row.status) == (
        "anthropic",
        "claude-opus-5-5",
        "test",
        "ok",
    )
    assert (row.input_tokens, row.output_tokens) == (3000, 1200)
    assert row.cost_usd == Decimal("0.036000")
    assert len(row.prompt_sha256) == 64


def test_fallbacks_can_be_turned_off(db: Database) -> None:
    client, fake = _client_and_fake(db, [parsed_response(Answer(value="ok"))], fallbacks=False)
    _call(client)
    assert "fallbacks" not in fake.calls[0]


def test_audit_records_the_model_that_answered(db: Database) -> None:
    client = _client(db, [parsed_response(Answer(value="ok"), model="claude-opus-4-8")])
    _call(client)
    assert _rows(db)[0].model == "claude-opus-4-8"


def test_refusal_raises_and_is_audited(db: Database) -> None:
    client = _client(db, [parsed_response(None, stop_reason="refusal", category="bio")])
    with pytest.raises(ModelRefusalError) as caught:
        _call(client)
    assert caught.value.category == "bio"
    (row,) = _rows(db)
    assert (row.status, row.error) == ("refusal", "refusal: bio")


def test_truncated_output_raises(db: Database) -> None:
    client = _client(db, [parsed_response(None, stop_reason="max_tokens")])
    with pytest.raises(LLMOutputError):
        _call(client)
    assert _rows(db)[0].status == "truncated"


def test_unparsed_output_raises(db: Database) -> None:
    client = _client(db, [parsed_response(None)])
    with pytest.raises(LLMOutputError):
        _call(client)
    assert _rows(db)[0].status == "invalid_output"


def test_api_errors_are_audited_and_reraised(db: Database) -> None:
    client = _client(db, [RuntimeError("connection reset")])
    with pytest.raises(RuntimeError):
        _call(client)
    (row,) = _rows(db)
    assert row.status == "error"
    assert row.error == "RuntimeError: connection reset"
