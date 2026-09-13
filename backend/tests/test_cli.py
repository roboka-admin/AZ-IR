"""The operational CLI must work without a database and must never lie about health."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from azir.cli import build_parser, command_doctor, command_seed, main
from azir.core.config import reset_settings_cache

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "seeds" / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch):
    """Each test gets its own environment; `get_settings` is cached for the process."""
    monkeypatch.setenv("AZIR_ENV", "development")
    monkeypatch.setenv("AZIR_DB_DRIVER", "fixtures")
    monkeypatch.setenv("AZIR_FIXTURES_DIR", str(FIXTURES_DIR))
    monkeypatch.delenv("AZIR_DB_URL", raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_parser_wires_every_command() -> None:
    parser = build_parser()
    assert parser.parse_args(["seed"]).func is command_seed
    assert parser.parse_args(["doctor"]).func is command_doctor
    assert parser.parse_args(["serve", "--port", "9000"]).port == 9000
    assert parser.parse_args(["migrate", "head"]).revision == "head"
    assert parser.parse_args(["seed", "--append"]).append is True


def test_parser_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_doctor_reports_fixtures_health(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--human-logs", "doctor"]) == 0
    captured = capsys.readouterr()
    out = captured.out
    assert captured.err.strip(), "diagnostics must go to stderr so stdout stays machine-readable"
    payload = json.loads(out[out.index("{") : out.rindex("}") + 1])
    assert payload["driver"] == "fixtures"
    assert payload["records"] > 0
    assert payload["stats"]["sources"] > 0
    assert payload["stats"]["provisional_geometries"] >= 0


def test_doctor_flags_an_unsafe_production_configuration(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production on the fixtures driver must fail the check, not silently serve demo data."""
    monkeypatch.setenv("AZIR_ENV", "production")
    monkeypatch.setenv("AZIR_DB_DRIVER", "fixtures")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="not allowed in production"):
        main(["--human-logs", "doctor"])
    capsys.readouterr()


def test_seed_refuses_without_a_database_url(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["seed"]) == 2
    assert "AZIR_DB_URL" in capsys.readouterr().err


def test_seed_points_at_the_fixtures_corpus_by_default() -> None:
    """The seeder normalizes with the same loader the dev driver uses (ADR-0014)."""
    from azir.repositories.fixtures import FixturesRepository

    loader = FixturesRepository(FIXTURES_DIR)
    records = loader.records
    assert records, "the fixture corpus must not be empty"
    assert loader.raw_documents["assertions"], "claims are the graph; they must be seedable"
    # Every record the seeder writes carries the domain-computed presentation aids.
    assert all(record.layer and record.rank >= 0 for record in records)


# ------------------------------------------------------------------ user + lint commands


def test_parser_wires_the_write_commands() -> None:
    from azir.cli import command_lint, command_user_add, command_user_list, command_user_sync_dev

    parser = build_parser()
    assert parser.parse_args(["user", "list"]).func is command_user_list
    assert parser.parse_args(["user", "add", "--email", "a@b.c"]).func is command_user_add
    assert parser.parse_args(["user", "sync-dev"]).func is command_user_sync_dev
    assert parser.parse_args(["lint", "--json"]).json is True
    assert parser.parse_args(["lint"]).func is command_lint
    with pytest.raises(SystemExit):
        parser.parse_args(["user"])  # a subcommand is required
    with pytest.raises(SystemExit):
        parser.parse_args(["user", "add", "--email", "a@b.c", "--role", "emperor"])


def test_user_list_never_prints_a_secret(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["user", "list"]) == 0
    printed = capsys.readouterr().out
    payload = json.loads(printed)
    assert payload["count"] == 4, "the fixtures driver builds its four dev identities at startup"
    assert {user["email"] for user in payload["users"]} >= {"admin@atlas.local"}
    for secret in ("argon2", "$2", "password", "atlas-dev-password"):
        assert secret not in printed.lower() or secret == "password"
    assert all("password" not in user for user in payload["users"])


def test_user_add_reads_the_password_from_stdin(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    from azir.services.registry import get_editorial_repository, reset_repository_cache

    monkeypatch.setattr("sys.stdin", io.StringIO("a-long-enough-password\n"))
    reset_repository_cache()
    try:
        code = main(
            ["user", "add", "--email", "cli@atlas.local", "--role", "reviewer", "--name", "از CLI"]
        )
        assert code == 0
        created = json.loads(capsys.readouterr().out)["created"]
        assert created["email"] == "cli@atlas.local" and created["role"] == "reviewer"
        repository = get_editorial_repository()
        assert repository.authenticate("cli@atlas.local", "a-long-enough-password") is not None
        # A CLI write is audited like any other write.
        assert any(
            entry.entity_id == created["id"]
            for entry in repository.audit(entity_type="user", limit=50)
        )
    finally:
        reset_repository_cache()


def test_user_sync_dev_is_idempotent_and_refuses_production(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from azir.core.config import reset_settings_cache

    assert main(["user", "sync-dev"]) == 0
    first = json.loads(capsys.readouterr().out)
    # The fixtures driver already has these four, so nothing is created -- and nothing crashes.
    assert first["created"] == []
    assert set(first["already_present"]) == {
        "admin@atlas.local",
        "reviewer@atlas.local",
        "editor@atlas.local",
        "contributor@atlas.local",
    }
    assert first["password_env"] == "AZIR_DEV_PASSWORD"

    monkeypatch.setenv("AZIR_ENV", "production")
    monkeypatch.setenv("AZIR_DB_DRIVER", "postgis")
    monkeypatch.setenv("AZIR_DB_URL", "postgresql+psycopg2://u:p@localhost/azir")
    reset_settings_cache()
    try:
        assert main(["user", "sync-dev"]) == 2
        assert "production" in capsys.readouterr().err
    finally:
        reset_settings_cache()


def test_lint_reports_and_exits_zero_on_a_clean_corpus(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["lint", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["data"]["scope"] == "corpus"
    assert report["data"]["errors"] == 0, "the seeded pilot corpus must pass its own gates"
    assert report["data"]["not_evaluated"], "the report admits what it does not check"


def test_lint_exits_one_when_something_blocks(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from azir.services.lint import DataLinter, Finding

    blocking = Finding(
        rule="D4",
        level="error",
        entity_type="place",
        entity_id="plc_x",
        message_fa="منبع ندارد.",
        message_en="No source.",
    )
    monkeypatch.setattr(DataLinter, "corpus", lambda self, *, limit=500: [blocking])
    assert main(["lint"]) == 1
    printed = capsys.readouterr().out
    assert "1 blocking" in printed and "D4" in printed and "منبع ندارد." in printed


def test_lint_can_target_one_record(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["lint", "--entity-id", "plc_ardabil", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["data"]["scope"].startswith("place:plc_ardabil")
    assert main(["lint", "--entity-id", "plc_nonsense"]) == 2
