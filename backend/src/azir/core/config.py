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

    # --- editorial panel (ADR-0010) ----------------------------------------
    #: None means "auto": the write API is on in development/staging and off in production, so a
    #: deploy never exposes it by accident. Set AZIR_EDITORIAL_ENABLED=1 to opt in explicitly.
    editorial_enabled: bool | None = None
    session_ttl_hours: int = 12
    session_cookie_name: str = "azir_session"
    csrf_cookie_name: str = "azir_csrf"
    csrf_header_name: str = "x-csrf-token"
    #: None means "auto": Secure cookies in production, plain ones on http://localhost.
    session_cookie_secure: bool | None = None
    #: "lax" for a same-site panel; "none" (with secure cookies) when the frontend is on another
    #: registrable domain than the API.
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    login_max_attempts: int = 5
    login_window_seconds: int = 300

    # --- tiles (ADR-0011) --------------------------------------------------
    #: Where ``azir tiles build`` writes archives. Served statically (or uploaded to object
    #: storage) -- the API never needs to read this directory to answer a tile request.
    tiles_dir: str = "tiles"
    tiles_min_zoom: int = 0
    #: Pilot default. Deeper zooms are built when the corpus has geometry that justifies them:
    #: an empty z14 pyramid is 16x the requests for nothing.
    tiles_max_zoom: int = 10
    #: How far outside the tile to query and clip, as a fraction of the tile span. Lines and
    #: labels must not stop dead at the seam; 10% is enough and keeps tiles small.
    tiles_buffer_ratio: float = 0.1
    #: Features per tile before the tile is degraded by rank (and reported, never silent).
    tiles_feature_limit: int = 4000
    #: Serve ``GET /api/v1/tiles/{z}/{x}/{y}.pbf``. On in development (no build step needed to see
    #: a map); in production the static archive on the CDN answers this and the API stays out of it.
    tiles_dynamic_enabled: bool = True
    #: Public URL prefix for the archive, written into ``latest.json``. None means "derive from
    #: public_base_url + /tiles", which is right for local development and wrong for a CDN -- set it
    #: (e.g. https://cdn.example.com/azir/tiles) when the archive is uploaded.
    tiles_public_base_url: str | None = None

    # --- study area (the atlas viewport default) ---------------------------
    study_area_bbox: tuple[float, float, float, float] = (44.0, 35.5, 49.5, 39.8)
    #: The wider area the corpus may legitimately reach into: places and events that Iranian
    #: Azerbaijan cannot be told without (Qazvin, where the Safavid capital moved in 1548; the
    #: Caucasus; eastern Anatolia). A geometry outside ``study_area_bbox`` is *context* and is
    #: reported as a warning; a geometry outside this box is a mistake and blocks publication
    #: (data lint rule D2, docs/07 §3).
    corpus_bbox: tuple[float, float, float, float] = (42.5, 34.0, 51.0, 41.3)
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

    @property
    def editorial_on(self) -> bool:
        """Is the write API available at all in this environment?"""
        if self.editorial_enabled is None:
            return self.env != "production"
        return self.editorial_enabled

    @property
    def session_ttl_seconds(self) -> int:
        return max(300, int(self.session_ttl_hours) * 3600)

    @property
    def cookie_secure(self) -> bool:
        """Secure flag for session cookies: always on in production, off for http://localhost."""
        if self.session_cookie_secure is None:
            return self.env == "production"
        return self.session_cookie_secure

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
        if self.editorial_on and self.env == "production" and not self.cookie_secure:
            raise RuntimeError(
                "the editorial API in production requires secure session cookies "
                "(AZIR_SESSION_COOKIE_SECURE=1)"
            )
        if not _contains(self.corpus_bbox, self.study_area_bbox):
            raise RuntimeError("AZIR_CORPUS_BBOX must contain AZIR_STUDY_AREA_BBOX")
        if self.editorial_on and self.session_ttl_hours > 24 * 30:
            raise RuntimeError("AZIR_SESSION_TTL_HOURS must be at most 720 (30 days)")


def _contains(
    outer: tuple[float, float, float, float], inner: tuple[float, float, float, float]
) -> bool:
    """Does one (min_lon, min_lat, max_lon, max_lat) box contain another?"""
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_consistency()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
