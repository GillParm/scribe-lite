from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from scribe.db import Database
from scribe.models import Encounter, EncounterSource, RetentionDeletion
from scribe.retention import purge_expired_audio
from scribe.storage import AudioStore
from tests.conftest import FakeClock


def _encounter_with_audio(db: Database, store: AudioStore, clock: FakeClock, name: str) -> str:
    stored = store.save(name, io.BytesIO(b"x" * 1000), ".wav", max_bytes=10_000)
    with db.session() as session:
        encounter = Encounter(
            title=name,
            source=EncounterSource.UPLOAD,
            created_at=clock(),
            audio_path=stored.relative_path,
            audio_bytes=stored.size,
        )
        session.add(encounter)
        session.flush()
        return encounter.id


def test_deletes_only_audio_older_than_window(
    db: Database, store: AudioStore, clock: FakeClock
) -> None:
    old = _encounter_with_audio(db, store, clock, "old")
    clock.advance(days=20)
    recent = _encounter_with_audio(db, store, clock, "recent")
    clock.advance(days=11)  # old is now 31 days old, recent 11

    report = purge_expired_audio(db, store, retention_days=30, now=clock())

    assert [d.encounter_id for d in report.deleted] == [old]
    assert report.deleted[0].audio_path == "old.wav"
    assert report.deleted[0].age_days == pytest.approx(31)
    assert report.bytes_freed == 1000
    assert not store.path("old.wav").exists()
    assert store.path("recent.wav").exists()
    with db.session() as session:
        old_row = session.get_one(Encounter, old)
        assert old_row.audio_path is None
        assert old_row.audio_deleted_at == clock.now
        assert session.get_one(Encounter, recent).audio_path == "recent.wav"
        logged = session.scalars(select(RetentionDeletion)).one()
        assert (logged.encounter_id, logged.audio_path, logged.retention_days) == (
            old,
            "old.wav",
            30,
        )


def test_dry_run_changes_nothing(db: Database, store: AudioStore, clock: FakeClock) -> None:
    encounter_id = _encounter_with_audio(db, store, clock, "a")
    clock.advance(days=40)

    report = purge_expired_audio(db, store, retention_days=30, now=clock(), dry_run=True)

    assert [d.encounter_id for d in report.deleted] == [encounter_id]
    assert store.path("a.wav").exists()
    with db.session() as session:
        assert session.get_one(Encounter, encounter_id).audio_path == "a.wav"
        assert session.scalars(select(RetentionDeletion)).all() == []


def test_second_run_is_a_no_op(db: Database, store: AudioStore, clock: FakeClock) -> None:
    _encounter_with_audio(db, store, clock, "a")
    clock.advance(days=31)
    assert len(purge_expired_audio(db, store, retention_days=30, now=clock()).deleted) == 1
    assert purge_expired_audio(db, store, retention_days=30, now=clock()).deleted == []


def test_boundary_is_exclusive(db: Database, store: AudioStore, clock: FakeClock) -> None:
    _encounter_with_audio(db, store, clock, "a")
    clock.advance(days=30)  # exactly 30 days: kept
    assert purge_expired_audio(db, store, retention_days=30, now=clock()).deleted == []
    clock.advance(seconds=1)
    assert len(purge_expired_audio(db, store, retention_days=30, now=clock()).deleted) == 1


def test_missing_file_is_still_marked_deleted(
    db: Database, store: AudioStore, clock: FakeClock
) -> None:
    encounter_id = _encounter_with_audio(db, store, clock, "gone")
    store.path("gone.wav").unlink()
    clock.advance(days=31)

    report = purge_expired_audio(db, store, retention_days=30, now=clock())

    assert [d.encounter_id for d in report.deleted] == [encounter_id]
    with db.session() as session:
        assert session.get_one(Encounter, encounter_id).audio_deleted_at == clock.now


def test_rejects_non_positive_window(db: Database, store: AudioStore, clock: FakeClock) -> None:
    with pytest.raises(ValueError):
        purge_expired_audio(db, store, retention_days=0, now=clock())
