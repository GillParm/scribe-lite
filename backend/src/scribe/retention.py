"""Delete audio older than the retention window.

Transcripts and notes are kept; only the recording goes. The encounter keeps a
timestamp of when its audio was deleted and a `retention_deletions` row records what
was removed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from scribe.db import Database
from scribe.models import Encounter, RetentionDeletion
from scribe.storage import AudioStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeletedAudio:
    encounter_id: str
    audio_path: str
    audio_bytes: int
    age_days: float


@dataclass
class RetentionReport:
    ran_at: datetime
    cutoff: datetime
    retention_days: int
    dry_run: bool
    deleted: list[DeletedAudio] = field(default_factory=list)

    @property
    def bytes_freed(self) -> int:
        return sum(d.audio_bytes for d in self.deleted)


def purge_expired_audio(
    db: Database,
    store: AudioStore,
    *,
    retention_days: int,
    now: datetime,
    dry_run: bool = False,
) -> RetentionReport:
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    cutoff = now - timedelta(days=retention_days)
    report = RetentionReport(
        ran_at=now, cutoff=cutoff, retention_days=retention_days, dry_run=dry_run
    )
    with db.session() as session:
        expired = session.scalars(
            select(Encounter)
            .where(Encounter.audio_path.is_not(None))
            .where(Encounter.audio_deleted_at.is_(None))
            .where(Encounter.created_at < cutoff)
            .order_by(Encounter.created_at)
        ).all()
        for encounter in expired:
            audio_path = encounter.audio_path
            assert audio_path is not None
            age_days = (now - encounter.created_at).total_seconds() / 86400
            freed = encounter.audio_bytes or 0
            if not dry_run:
                freed = store.delete(audio_path) or freed
                session.add(
                    RetentionDeletion(
                        deleted_at=now,
                        encounter_id=encounter.id,
                        audio_path=audio_path,
                        audio_bytes=freed,
                        age_days=round(age_days, 3),
                        retention_days=retention_days,
                    )
                )
                encounter.audio_path = None
                encounter.audio_deleted_at = now
            report.deleted.append(
                DeletedAudio(
                    encounter_id=encounter.id,
                    audio_path=audio_path,
                    audio_bytes=freed,
                    age_days=round(age_days, 3),
                )
            )
            log.info(
                "retention %s audio for encounter %s (%.1f days old, %d bytes)",
                "would delete" if dry_run else "deleted",
                encounter.id,
                age_days,
                freed,
            )
    log.info(
        "retention run: %d file(s) %s, cutoff %s",
        len(report.deleted),
        "eligible" if dry_run else "deleted",
        cutoff.isoformat(),
    )
    return report
