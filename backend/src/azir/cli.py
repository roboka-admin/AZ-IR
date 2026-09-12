"""Operational CLI: ``python -m azir.cli {seed,doctor,serve,migrate}``.

Deliberately dependency-free (argparse only, AGENTS.md rule 14). Every command is safe to run in
CI: nothing writes unless ``seed`` is asked to, and ``doctor`` never mutates data.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

from .core.config import Settings, get_settings
from .core.logging import configure_logging

logger = logging.getLogger("azir.cli")


# ------------------------------------------------------------------ commands


def command_seed(args: argparse.Namespace, settings: Settings) -> int:
    """Load the fixture corpus into PostgreSQL+PostGIS."""
    from .repositories.postgis import build_engine, seed_database

    db_url = args.db_url or settings.db_url
    if not db_url:
        print("AZIR_DB_URL is not set; nothing to seed.", file=sys.stderr)
        return 2
    engine = build_engine(db_url, settings)
    report = seed_database(engine, args.fixtures_dir or settings.fixtures_dir, truncate=not args.append)
    engine.dispose()
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


def command_doctor(args: argparse.Namespace, settings: Settings) -> int:
    """Report configuration and data health. Exit code 1 means "do not deploy this"."""
    problems: list[str] = []
    info: dict[str, Any] = {
        "env": settings.env,
        "driver": settings.db_driver,
        "fixtures_dir": settings.fixtures_dir,
        "study_area_bbox": list(settings.study_area_bbox),
    }

    if settings.db_driver == "postgis":
        from sqlalchemy import text

        from .repositories.postgis import build_engine

        if not settings.db_url:
            problems.append("AZIR_DB_DRIVER=postgis but AZIR_DB_URL is empty")
        else:
            engine = build_engine(settings.db_url, settings)
            with engine.connect() as connection:
                info["postgis"] = connection.execute(text("SELECT postgis_lib_version()")).scalar()
                info["server"] = connection.execute(text("SHOW server_version")).scalar()
                info["alembic"] = connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num DESC LIMIT 1")
                ).scalar()
                for name in ("place", "person", "event", "political_entity", "article",
                             "source", "period", "assertion", "evidence", "entity_geometry",
                             "name_variant"):
                    info[f"rows.{name}"] = connection.execute(
                        text(f"SELECT count(*) FROM public.{name}")
                    ).scalar()
                # Data lint: the rules that keep the atlas honest (docs/09).
                lint = {
                    "assertions_without_evidence": connection.execute(
                        text(
                            "SELECT count(*) FROM assertion a "
                            "WHERE a.status <> 'rejected' AND NOT EXISTS "
                            "(SELECT 1 FROM evidence e WHERE e.assertion_id = a.id)"
                        )
                    ).scalar(),
                    "disputed_without_topic": connection.execute(
                        text("SELECT count(*) FROM assertion WHERE status = 'disputed' AND topic_fa IS NULL")
                    ).scalar(),
                    "published_without_name": connection.execute(
                        text(
                            "SELECT count(*) FROM entity_read_model erm "
                            "WHERE erm.status = 'published' AND NOT EXISTS "
                            "(SELECT 1 FROM name_variant nv WHERE nv.entity_id = erm.id)"
                        )
                    ).scalar(),
                    "published_without_geometry": connection.execute(
                        text(
                            "SELECT count(*) FROM entity_read_model erm "
                            "WHERE erm.status = 'published' AND erm.entity_type <> 'article' "
                            "AND NOT EXISTS (SELECT 1 FROM entity_geometry g WHERE g.entity_id = erm.id)"
                        )
                    ).scalar(),
                    "reconstructed_marked_exact": connection.execute(
                        text(
                            "SELECT count(*) FROM entity_geometry "
                            "WHERE kind = 'extent_reconstructed' AND certainty = 'exact'"
                        )
                    ).scalar(),
                    "needs_digitisation": connection.execute(
                        text("SELECT count(*) FROM entity_geometry WHERE needs_digitisation")
                    ).scalar(),
                }
                info["lint"] = lint
                for key in ("assertions_without_evidence", "disputed_without_topic",
                            "published_without_name", "reconstructed_marked_exact"):
                    if lint[key]:
                        problems.append(f"{key}: {lint[key]}")
            engine.dispose()
    else:
        from .repositories.fixtures import FixturesRepository

        repository = FixturesRepository(settings.fixtures_dir)
        info["records"] = len(repository.records)
        info["stats"] = repository.stats()

    print(json.dumps(info, ensure_ascii=False, indent=2, default=str))
    if problems:
        print("\nPROBLEMS:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("\nOK")
    return 0


def command_serve(args: argparse.Namespace, settings: Settings) -> int:
    """Run the API with uvicorn (development convenience; production uses the same entrypoint)."""
    import uvicorn

    uvicorn.run(
        "azir.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=False,
        app_dir=None,
    )
    return 0


def command_migrate(args: argparse.Namespace, settings: Settings) -> int:
    """Thin wrapper over ``alembic upgrade head`` so one entrypoint covers every operation."""
    db_url = args.db_url or settings.db_url or ""
    env = {**os.environ, "AZIR_DB_URL": db_url}
    command = [sys.executable, "-m", "alembic", "upgrade", args.revision]
    print(" ".join(command))
    return subprocess.call(command, env=env)


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="azir", description="AZ-IR historical atlas operations")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--human-logs", action="store_true", help="plain text instead of JSON logs")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="load the fixture corpus into PostgreSQL+PostGIS")
    seed.add_argument("--db-url", default=None)
    seed.add_argument("--fixtures-dir", default=None)
    seed.add_argument("--append", action="store_true", help="keep existing rows (default: truncate)")
    seed.set_defaults(func=command_seed)

    doctor = sub.add_parser("doctor", help="report configuration and data health")
    doctor.set_defaults(func=command_doctor)

    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=command_serve)

    migrate = sub.add_parser("migrate", help="apply database migrations (alembic upgrade)")
    migrate.add_argument("--db-url", default=None)
    migrate.add_argument("revision", nargs="?", default="head")
    migrate.set_defaults(func=command_migrate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(level=args.log_level, json_output=not args.human_logs)
    logger.info("azir cli: %s", args.command)
    return int(args.func(args, settings))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
