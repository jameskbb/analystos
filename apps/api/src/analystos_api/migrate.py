"""Programmatic Alembic helpers (used at startup and in tests)."""

from __future__ import annotations

from importlib.resources import files

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(files("analystos_api") / "migrations"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade_to_head(engine: Engine) -> None:
    cfg = alembic_config(engine.url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


def cli() -> None:
    """Console script: ``uv run analystos-api-migrate`` upgrades DATABASE_URL to head."""
    from .config import get_settings
    from .db import make_engine

    engine = make_engine(get_settings().database_url)
    upgrade_to_head(engine)
    print("Database upgraded to head")
