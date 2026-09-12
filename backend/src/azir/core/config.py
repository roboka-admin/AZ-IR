"""Runtime configuration. 12-factor: everything comes from the environment (``AZIR_*``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Driver = Literal["fixtures", "postgis"]
Env = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AZIR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AZ-IR Historical Atlas"
    api_prefix: str = "/api/v1"
    api_version: str = "1"
    env: Env = "development"
    debug: bool = False

    # --- data access -------------------------------------------------------
    db_driver: Driver = "fixtures"
    db_url: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_statement_timeout_ms: int = 5000
    fixtures_dir: str = "backend/seeds/fixtures"

    # --- api ---------------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    default_locale: str = "fa"
    supported_locales: list[str] = Field(default_factory=lambda: ["fa", "en"])
    max_limit: int = 2000
    default_limit: int = 800
    payload_budget_bytes: int = 512 * 1024
    cache_ttl_seconds: int = 60
    entity_cache_ttl_seconds: int = 300
    rate_limit_per_minute: int = 60
    public_base_url: str = "http://localhost:8000"

    # --- study area (the atlas viewport default) ---------------------------
    study_area_bbox: tuple[float, float, float, float] = (44.0, 35.5, 49.5, 39.8)
    study_area_center: tuple[float, float] = (47.0, 38.0)
    study_area_default_zoom: float = 6.4
    study_area_name_fa: str = "آذربایجان ایران"
    study_area_name_en: str = "Iranian Azerbaijan"

    # --- temporal ----------------------------------------------------------
    timeline_floor: int = -800
    timeline_ceil: int = 2026
    timeline_default_year: int = 1500
    timeline_buckets: list[int] = Field(default_factory=lambda: [1, 5, 25, 100, 500])
    circa_fuzz_years: int = 10

    @field_validator("fixtures_dir")
    @classmethod
    def _resolve_fixtures_dir(cls, value: str) -> str:
        """Resolve a relative fixtures path against the CWD, then the repository root.

        The default is written relative to the repo root because that reads well in
        ``.env.example``, but the API is normally started from ``backend/``. Without this the dev
        driver would boot or fail depending on where the command happened to be typed.
        """
        if not value.strip():  # an empty AZIR_FIXTURES_DIR means "use the default"
            value = "backend/seeds/fixtures"
        path = Path(value)
        if path.is_absolute() or path.is_dir():
            return str(path)
        here = Path(__file__).resolve()
        for base in (here.parents[3], here.parents[4], here.parents[5]):
            candidate = base / path
            if candidate.is_dir():
                return str(candidate)
        return str(path)

    def validate_consistency(self) -> None:
        """Fail fast on unsafe configuration instead of serving wrong data."""
        if self.env == "production" and self.db_driver == "fixtures":
            raise RuntimeError(
                "AZIR_DB_DRIVER=fixtures is not allowed in production (ADR-0014); "
                "point AZIR_DB_URL at PostgreSQL+PostGIS"
            )
        if self.db_driver == "postgis" and not self.db_url:
            raise RuntimeError("AZIR_DB_DRIVER=postgis requires AZIR_DB_URL")
        if self.default_locale not in self.supported_locales:
            raise RuntimeError("AZIR_DEFAULT_LOCALE must be listed in AZIR_SUPPORTED_LOCALES")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_consistency()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
