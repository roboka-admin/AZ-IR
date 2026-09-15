"""Temporal invariants: precision drives the interval, never the other way round."""

from __future__ import annotations

import pytest

from azir.domain.enums import Confidence, Precision
from azir.domain.temporal import (
    RESEARCH_CEIL,
    RESEARCH_FLOOR,
    TemporalInterval,
    TemporalMode,
    TimeWindow,
    bce_to_astronomical,
    century_bounds,
    century_number,
    derive_interval,
    format_interval,
)


def test_circa_is_never_stored_as_an_exact_year() -> None:
    interval = derive_interval(year=1400, precision=Precision.CIRCA_YEAR)
    assert (interval.year_from, interval.year_to) == (1390, 1410)
    assert interval.precision is Precision.CIRCA_YEAR
    assert "circa" in format_interval(interval, "en").lower() or "c." in format_interval(interval, "en")


@pytest.mark.parametrize(
    ("year", "precision", "expected"),
    [
        (1450, Precision.EXACT_YEAR, (1450, 1450)),
        (1400, Precision.CIRCA_YEAR, (1390, 1410)),
        (1455, Precision.DECADE, (1450, 1459)),
        (1455, Precision.CENTURY, (1401, 1500)),
        (8, Precision.CENTURY, (1, 100)),
        (1450, Precision.BEFORE, (RESEARCH_FLOOR, 1450)),
        (1450, Precision.AFTER, (1450, RESEARCH_CEIL)),
        (-550, Precision.CENTURY, (-600, -501)),
    ],
)
def test_fuzz_table(year: int, precision: Precision, expected: tuple[int, int]) -> None:
    interval = derive_interval(year=year, precision=precision)
    assert (interval.year_from, interval.year_to) == expected


def test_range_requires_an_end() -> None:
    with pytest.raises(ValueError):
        derive_interval(year=1400, precision=Precision.RANGE)


def test_unknown_date_is_never_confident() -> None:
    interval = TemporalInterval.unknown()
    assert interval.confidence in (Confidence.LOW, Confidence.DISPUTED)
    assert format_interval(interval, "fa") == "تاریخ نامعلوم"


def test_inverted_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        TemporalInterval(1500, 1400)


def test_interval_algebra() -> None:
    a = TemporalInterval(1400, 1500)
    b = TemporalInterval(1450, 1550)
    c = TemporalInterval(1600, 1700)
    assert a.overlaps(b) and not a.overlaps(c)
    assert TemporalInterval(1300, 1800).contains(a)
    assert not a.contains(b)
    assert a.intersection(b) == TemporalInterval(1450, 1500, Precision.RANGE, a.calendar, a.confidence)
    assert a.distance_to_year(1450) == 0
    assert a.distance_to_year(1510) == 10


@pytest.mark.parametrize("mode", [TemporalMode.AT, TemporalMode.DURING, TemporalMode.OVERLAPS])
def test_window_modes(mode: str) -> None:
    interval = TemporalInterval(1490, 1510)
    assert TimeWindow.at(1500).overlaps(interval)
    assert TimeWindow.span(1400, 1600, TemporalMode.DURING).contains(interval)
    assert not TimeWindow.span(1495, 1505, TemporalMode.DURING).contains(interval)
    assert TimeWindow.span(1495, 1505, TemporalMode.OVERLAPS).overlaps(interval)


def test_window_bucketing_is_cache_friendly() -> None:
    bucketed = TimeWindow.at(1453).bucket(25)
    assert (bucketed.year_from, bucketed.year_to) == (1450, 1474)
    assert TimeWindow.at(1451).bucket(25) == bucketed


def test_astronomical_year_numbering() -> None:
    assert bce_to_astronomical(1) == 0
    assert bce_to_astronomical(550) == -549
    assert century_number(-549) == 6
    assert century_bounds(1) == (1, 100)
    assert century_number(2026) == 21
