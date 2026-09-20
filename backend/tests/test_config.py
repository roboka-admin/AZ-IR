"""Configuration names are deployment contracts: ignored AZIR_* values are production bugs."""

import pytest

from azir.core.config import Settings


def test_documented_runtime_settings_are_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("AZIR_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("AZIR_PAYLOAD_BUDGET_BYTES", "262144")
    monkeypatch.setenv("AZIR_STUDY_AREA_DEFAULT_ZOOM", "7.25")

    settings = Settings(_env_file=None)

    assert settings.log_level == "WARNING"
    assert settings.payload_budget_bytes == 262144
    assert settings.study_area_default_zoom == 7.25


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"default_limit": 100, "max_limit": 10}, "DEFAULT_LIMIT"),
        ({"timeline_floor": 1600, "timeline_default_year": 1500}, "TIMELINE_DEFAULT_YEAR"),
        ({"tiles_min_zoom": 12, "tiles_max_zoom": 10}, "tile zooms"),
    ],
)
def test_unsafe_runtime_ranges_fail_fast(overrides: dict[str, int], message: str) -> None:
    settings = Settings(_env_file=None, **overrides)
    with pytest.raises(RuntimeError, match=message):
        settings.validate_consistency()
