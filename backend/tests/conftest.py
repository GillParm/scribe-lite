"""Shared fixtures.

Tests run against TEST_DATABASE_URL when it is set (CI uses a Postgres service) and
fall back to a throwaway SQLite file otherwise, so `uv run pytest` works offline.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scribe.db import Database
from scribe.redaction import Redactor
from scribe.storage import AudioStore


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 1, 9, 0, tzinfo=UTC))


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    database = Database(url)
    database.drop_all()
    database.create_all()
    try:
        yield database
    finally:
        database.drop_all()
        database.dispose()


@pytest.fixture
def store(tmp_path: Path) -> AudioStore:
    return AudioStore(tmp_path / "audio")


@pytest.fixture(scope="session")
def redactor() -> Redactor:
    # spaCy takes a couple of seconds to load; share one instance across the run.
    return Redactor("en_core_web_md")
