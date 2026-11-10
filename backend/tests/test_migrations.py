"""The Alembic migrations build exactly the schema the ORM models describe."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from scribe.db import Base

BACKEND = Path(__file__).resolve().parents[1]


def test_upgrade_head_matches_models(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")
    command.check(config)  # raises if autogenerate would emit any operation

    tables = set(inspect(create_engine(url)).get_table_names())
    assert set(Base.metadata.tables) <= tables

    command.downgrade(config, "base")
    assert inspect(create_engine(url)).get_table_names() == ["alembic_version"]
