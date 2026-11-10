"""Audit log of model calls.

Every call to a hosted model goes through `AuditLog.track`, which writes one
`model_calls` row whether the call succeeds, raises or is refused. Rows are written in
their own transaction so a failed pipeline run still leaves its audit trail.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from scribe.clock import Clock, utcnow
from scribe.db import Database
from scribe.models import ModelCall
from scribe.pricing import Usage, cost_usd

log = logging.getLogger(__name__)


def prompt_sha256(payload: Any) -> str:
    """SHA-256 of the canonical JSON form of everything that shaped the prompt."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class CallTracker:
    """Filled in by the caller while a tracked call runs."""

    model: str
    usage: Usage = field(default_factory=Usage)
    status: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class AuditTotals:
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class AuditLog:
    def __init__(self, db: Database, clock: Clock = utcnow) -> None:
        self.db = db
        self.clock = clock

    def record(
        self,
        *,
        provider: str,
        model: str,
        purpose: str,
        prompt_hash: str,
        usage: Usage,
        latency_ms: int,
        status: str,
        encounter_id: str | None = None,
        error: str | None = None,
    ) -> ModelCall:
        row = ModelCall(
            created_at=self.clock(),
            provider=provider,
            model=model,
            purpose=purpose,
            encounter_id=encounter_id,
            prompt_sha256=prompt_hash,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cost_usd=cost_usd(model, usage),
            latency_ms=latency_ms,
            status=status,
            error=error[:2000] if error else None,
        )
        with self.db.session() as session:
            session.add(row)
        log.info(
            "model call provider=%s model=%s purpose=%s status=%s in=%d out=%d cost=%s",
            provider,
            model,
            purpose,
            status,
            usage.input_tokens,
            usage.output_tokens,
            row.cost_usd,
        )
        return row

    @contextmanager
    def track(
        self,
        *,
        provider: str,
        model: str,
        purpose: str,
        prompt: Any,
        encounter_id: str | None = None,
    ) -> Iterator[CallTracker]:
        """Time a model call and record it. Set `tracker.usage` once the response is in."""
        tracker = CallTracker(model=model)
        prompt_hash = prompt_sha256(prompt)
        started = time.perf_counter()
        try:
            yield tracker
        except BaseException as exc:
            self.record(
                provider=provider,
                model=tracker.model,
                purpose=purpose,
                prompt_hash=prompt_hash,
                usage=tracker.usage,
                latency_ms=_elapsed_ms(started),
                status=tracker.status or "error",
                encounter_id=encounter_id,
                error=tracker.error or f"{type(exc).__name__}: {exc}",
            )
            raise
        self.record(
            provider=provider,
            model=tracker.model,
            purpose=purpose,
            prompt_hash=prompt_hash,
            usage=tracker.usage,
            latency_ms=_elapsed_ms(started),
            status=tracker.status or "ok",
            encounter_id=encounter_id,
            error=tracker.error,
        )

    def totals(self) -> AuditTotals:
        with self.db.session() as session:
            row = session.execute(
                select(
                    func.count(ModelCall.id),
                    func.coalesce(func.sum(ModelCall.input_tokens), 0),
                    func.coalesce(func.sum(ModelCall.output_tokens), 0),
                    func.coalesce(func.sum(ModelCall.cost_usd), 0),
                )
            ).one()
        return AuditTotals(
            calls=int(row[0]),
            input_tokens=int(row[1]),
            output_tokens=int(row[2]),
            cost_usd=float(row[3] or 0),
        )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
