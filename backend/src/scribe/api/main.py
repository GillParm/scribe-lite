"""The FastAPI application."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from scribe import __version__
from scribe.api import encounters, system
from scribe.api.state import DEMO_NOTICE, AppState
from scribe.clock import utcnow
from scribe.config import Settings, get_settings
from scribe.db import Database
from scribe.encounters import fail_interrupted, seed_samples
from scribe.pipeline import Pipeline, Services
from scribe.retention import purge_expired_audio
from scribe.samples import load_sample_outputs, load_samples
from scribe.services import build_services

log = logging.getLogger(__name__)

DESCRIPTION = f"""\
**{DEMO_NOTICE}**

scribe-lite turns a recorded doctor-patient conversation into a SOAP note in which every
statement links to the transcript segments it came from. Personal details are replaced
with placeholders before any text reaches a language model, a second model pass flags
statements the transcript does not support, and every model call is audited.
"""


async def _retention_loop(state: AppState) -> None:
    interval = state.settings.retention_interval_minutes * 60
    while True:
        try:
            await asyncio.to_thread(
                purge_expired_audio,
                state.db,
                state.services.store,
                retention_days=state.settings.retention_days,
                now=utcnow(),
            )
        except Exception:
            log.exception("retention run failed")
        await asyncio.sleep(interval)


def _seed(state: AppState) -> None:
    samples = load_samples(state.settings.samples_dir)
    created = seed_samples(
        state.db,
        state.services.store,
        samples,
        max_bytes=state.settings.max_upload_mb * 1024 * 1024,
        outputs=load_sample_outputs(samples),
    )
    if created:
        log.info("seeded %d sample encounter(s)", len(created))


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and services (with fakes)."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = services.db if services else Database(settings.database_url)
        built = services or build_services(settings, db)
        state = AppState(settings=settings, db=db, services=built, pipeline=Pipeline(built))
        app.state.scribe = state
        await asyncio.to_thread(fail_interrupted, db)
        if settings.seed_samples:
            await asyncio.to_thread(_seed, state)
        task = None
        if settings.retention_schedule_enabled:
            task = asyncio.create_task(_retention_loop(state))
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if services is None:
                db.dispose()

    app = FastAPI(
        title="scribe-lite API (demo only, not for clinical use)",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )
    app.include_router(system.router)
    app.include_router(encounters.router)
    return app


def _app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return create_app()


app = _app()
