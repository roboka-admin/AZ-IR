"""Shared fixtures. The contract suite runs against every available driver (ADR-0014)."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azir.core.config import Settings, reset_settings_cache
from azir.repositories.fixtures import FixturesRepository
from azir.repositories.ports import AtlasRepository

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "seeds" / "fixtures"


@pytest.fixture(scope="session")
def settings() -> Iterator[Settings]:
    reset_settings_cache()
    yield Settings(
        env="development",
        db_driver="fixtures",
        fixtures_dir=str(FIXTURES_DIR),
        public_base_url="http://testserver",
    )
    reset_settings_cache()


@pytest.fixture(scope="session")
def fixtures_repo() -> Iterator[FixturesRepository]:
    yield FixturesRepository(FIXTURES_DIR)


def build_repositories() -> list[AtlasRepository]:
    """Every driver available in this environment; contract tests run against all of them."""
    repos: list[AtlasRepository] = [FixturesRepository(FIXTURES_DIR)]
    url = os.environ.get("AZIR_TEST_DB_URL")
    if url:  # pragma: no cover - only when CI provides a PostGIS database
        try:
            from azir.repositories.postgis import PostgisRepository

            repos.append(PostgisRepository(url))
        except Exception as exc:
            print(f"[contract] skipping postgis driver: {exc}")
    return repos


@pytest.fixture(scope="session")
def repositories() -> Iterator[list[AtlasRepository]]:
    yield build_repositories()


@pytest.fixture()
def client(settings: Settings):
    from fastapi.testclient import TestClient

    from azir.main import create_app

    os.environ.setdefault("AZIR_FIXTURES_DIR", str(FIXTURES_DIR))
    with TestClient(create_app(settings)) as test_client:
        yield test_client
