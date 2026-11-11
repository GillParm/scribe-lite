"""Per-process application state, reachable from request handlers."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from scribe.config import Settings
from scribe.db import Database
from scribe.pipeline import Pipeline, Services

DEMO_NOTICE = (
    "Demo only. Not for clinical use. Use synthetic data only; never upload real "
    "patient recordings."
)


@dataclass
class AppState:
    settings: Settings
    db: Database
    services: Services
    pipeline: Pipeline


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.scribe
    return state
