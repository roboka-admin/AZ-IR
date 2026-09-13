"""Operational CLI: ``python -m azir.cli {seed,doctor,serve,migrate,user,lint}``.

Deliberately dependency-free (argparse only, AGENTS.md rule 14). Every command is safe to run in
CI: nothing writes unless ``seed``/``user`` is asked to, and ``doctor``/``lint`` never mutate data.

``user`` and ``lint`` exist because the alternative is worse: an account created by hand-written SQL
is an account with no audit row, and a lint rule nobody can run on demand is a rule nobody believes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
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


def command_user_list(args: argparse.Namespace, settings: Settings) -> int:
    """Print the team. No password hash ever leaves the repository."""
    repository = _editorial(settings)
    people = repository.list_users()
    print(
        json.dumps(
            {
                "count": len(people),
                "driver": repository.driver_name,
                "users": [
                    {
                        "id": person.id,
                        "email": person.email,
                        "display_name": person.display_name,
                        "role": person.role.value,
                        "is_active": person.is_active,
                    }
                    for person in people
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_user_add(args: argparse.Namespace, settings: Settings) -> int:
    """Create one account. The password is read from stdin or a file, never from argv."""
    from .domain.editorial import Action, Role

    repository = _editorial(settings)
    password = _read_password(args)
    try:
        role = Role(args.role)
    except ValueError:
        print(f"unknown role {args.role!r}; expected one of {[r.value for r in Role]}", file=sys.stderr)
        return 2
    created = repository.create_user(
        email=args.email, display_name=args.name or args.email, role=role, password=password
    )
    # A CLI write is still a write: it lands in the same trail as a panel action (ADR-0010 rule 5).
    repository.record_audit(
        actor=None,
        action=Action.MANAGE_USERS,
        entity_type="user",
        entity_id=created.id,
        payload={"created": created.email, "role": created.role.value, "via": "cli"},
    )
    print(
        json.dumps(
            {
                "created": {
                    "id": created.id,
                    "email": created.email,
                    "display_name": created.display_name,
                    "role": created.role.value,
                },
                "driver": repository.driver_name,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if repository.driver_name == "fixtures":
        print(
            "warning: the fixtures driver keeps users in memory only; this account disappears when "
            "the process exits. Use AZIR_DB_DRIVER=postgis to persist it.",
            file=sys.stderr,
        )
    return 0


def command_user_sync_dev(args: argparse.Namespace, settings: Settings) -> int:
    """Create the development identities from ``10-users.yaml`` (idempotent, never in production).

    The fixtures driver builds these four accounts from the same file at startup; this command does
    the equivalent on PostgreSQL, so a developer signs in with the same address and the same
    ``AZIR_DEV_PASSWORD`` whichever driver is running.
    """
    import yaml

    from .domain.editorial import Action, Role

    if settings.env == "production":
        print("refusing to create development identities in production", file=sys.stderr)
        return 2
    repository = _editorial(settings)
    path = Path(args.users_file or Path(settings.fixtures_dir) / "10-users.yaml")
    if not path.exists():
        print(f"no users file at {path}", file=sys.stderr)
        return 2
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    password = os.environ.get(DEV_PASSWORD_ENV, DEV_PASSWORD_DEFAULT)
    created: list[str] = []
    skipped: list[str] = []
    for entry in document.get("users") or ():
        email = str(entry.get("email") or "").strip()
        if not email:
            continue
        if repository.authenticate(email, password) is not None:
            skipped.append(email)
            continue
        repository.create_user(
            email=email,
            display_name=str(entry.get("display_name") or email),
            role=Role(str(entry.get("role") or "editor")),
            password=password,
        )
        repository.record_audit(
            actor=None,
            action=Action.MANAGE_USERS,
            entity_type="user",
            payload={"created": email, "role": entry.get("role"), "via": "cli-sync-dev"},
        )
        created.append(email)
    print(
        json.dumps(
            {"created": created, "already_present": skipped, "password_env": DEV_PASSWORD_ENV},
            ensure_ascii=False,
            indent=2,
        )
    )
    if repository.driver_name == "fixtures":
        print("warning: the fixtures driver does not persist these accounts.", file=sys.stderr)
    return 0


def command_lint(args: argparse.Namespace, settings: Settings) -> int:
    """Run the data linter (docs/07 §3). Exit 1 when something blocks publication."""
    from .services.lint import DataLinter
    from .services.registry import get_repository

    repository = get_repository()
    linter = DataLinter(repository, settings)
    if args.entity_id:
        record = repository.entity(args.entity_type or _type_from_id(args.entity_id), args.entity_id)
        if record is None:
            print(f"no such entity: {args.entity_id}", file=sys.stderr)
            return 2
        findings = linter.record(record)
        scope = f"{record.entity_type.value}:{record.id}"
    else:
        findings = linter.corpus(limit=args.limit)
        scope = "corpus"
    report = linter.report(findings, scope=scope)
    if args.record:
        editorial = _editorial(settings)
        report["data"]["lint_run_id"] = editorial.record_lint(
            scope=scope, findings=[item.as_dict() for item in findings], actor=None
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        data = report["data"]
        print(
            f"scope {scope}: {data['errors']} error(s), {data['warnings']} warning(s), "
            f"{data['blocking']} blocking"
        )
        for item in findings:
            marker = "✗" if item.level == "error" else "!"
            print(f"  {marker} {item.rule:<4} {item.entity_id or '-':<28} {item.message_fa}")
            if item.message_en:
                print(f"        {item.message_en}")
        for gap in data["not_evaluated"]:
            print(f"  · {gap['rule']:<4} not machine-checked: {gap['reason']}")
    return 1 if report["data"]["blocking"] else 0


#: The development password is an environment variable, never a file in the repository.
DEV_PASSWORD_ENV = "AZIR_DEV_PASSWORD"
DEV_PASSWORD_DEFAULT = "atlas-dev-password"


def _editorial(settings: Settings) -> Any:
    """The write-side repository, or a clear message when this driver has none."""
    from .services.registry import get_editorial_repository

    if not settings.editorial_on:
        print(
            "the editorial write layer is disabled in this environment "
            "(set AZIR_EDITORIAL_ENABLED=1 to override)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return get_editorial_repository()


def _read_password(args: argparse.Namespace) -> str:
    """Stdin, a file, or a prompt -- in that order. Never argv: ``ps`` shows argv to every user."""
    if getattr(args, "password_file", None):
        return Path(args.password_file).read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        password = sys.stdin.read().strip()
        if password:
            return password
    import getpass

    return getpass.getpass("password: ").strip()


def _type_from_id(entity_id: str) -> str:
    from .core.ids import PREFIX

    prefix = entity_id.split("_", 1)[0] if "_" in entity_id else ""
    for entity_type, short in PREFIX.items():
        if short == prefix:
            return str(entity_type)
    return "place"


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

    user = sub.add_parser("user", help="manage editorial accounts")
    user_sub = user.add_subparsers(dest="user_command", required=True)
    user_list = user_sub.add_parser("list", help="print the team as JSON")
    user_list.set_defaults(func=command_user_list)
    user_add = user_sub.add_parser("add", help="create an account (password from stdin or a file)")
    user_add.add_argument("--email", required=True)
    user_add.add_argument("--name", default=None, help="display name (defaults to the email)")
    user_add.add_argument(
        "--role", default="editor", choices=["admin", "reviewer", "editor", "contributor"]
    )
    user_add.add_argument("--password-file", default=None, help="read the password from this file")
    user_add.set_defaults(func=command_user_add)
    user_dev = user_sub.add_parser(
        "sync-dev", help="create the development identities from 10-users.yaml"
    )
    user_dev.add_argument("--users-file", default=None)
    user_dev.set_defaults(func=command_user_sync_dev)

    lint = sub.add_parser("lint", help="run the data-quality rules (docs/07 §3)")
    lint.add_argument("--entity-type", default=None)
    lint.add_argument("--entity-id", default=None, help="lint one record instead of the corpus")
    lint.add_argument(
        "--limit", type=int, default=500, help="how many records of the corpus to scan"
    )
    lint.add_argument("--json", action="store_true", help="the full report instead of a summary")
    lint.add_argument("--record", action="store_true", help="persist the run (lint_run table)")
    lint.set_defaults(func=command_lint)
    return parser


def _logs_to_stderr() -> None:
    """A CLI's stdout is data (``doctor`` prints JSON); diagnostics belong on stderr."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(level=args.log_level, json_output=not args.human_logs)
    _logs_to_stderr()
    logger.info("azir cli: %s", args.command)
    return int(args.func(args, settings))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
