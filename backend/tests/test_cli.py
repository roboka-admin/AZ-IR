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
