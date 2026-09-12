"""Alembic environment.

Migrations are hand-written frozen DDL (ADR-0002); this file only wires the URL and the metadata
used for `--autogenerate` review. The URL comes from AZIR_DB_URL so no credential is ever
committed.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from azir.repositories.postgis.schema import METADATA  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def database_url() -> str:
    """AZIR_DB_URL wins; alembic.ini's value is only a local default."""
    from azir.core.config import get_settings

    url = os.environ.get("AZIR_DB_URL")
    if url:
        return url
    try:
        settings_url = get_settings().db_url
    except Exception:  # pragma: no cover - misconfigured env falls back to alembic.ini
        settings_url = None
    return settings_url or config.get_main_option("sqlalchemy.url") or ""


target_metadata = METADATA


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
