"""Golden tests for the calendar engine (ADR-0005). Values verified against published anchors."""

from __future__ import annotations

import pytest

from azir.domain.calendar import (
    CalendarDate,
    convert,
    gregorian_leap,
    hijri_year_span,
    islamic_to_jdn,
    jdn_to_islamic,
    jdn_to_persian,
    month_length,
    persian_leap,
    persian_to_jdn,
    to_gregorian_year_range,
)
from azir.domain.enums import Calendar


def _greg(year: int, month: int, day: int) -> tuple[int, int, int]:
    start, end = convert(CalendarDate(Calendar.GREGORIAN_PROLEPTIC, year, month, day), Calendar.GREGORIAN_PROLEPTIC)
    return (start.year, start.month, start.day), (end.year, end.month, end.day)  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("ah_year", "ah_month", "ah_day", "expected"),
    [
        (1445, 1, 1, (2023, 7, 19)),   # 1 Muharram 1445 AH
        (907, 1, 1, (1501, 7, 27)),    # 1 Muharram 907 AH, near Ismail's entry into Tabriz
        (735, 1, 1, (1334, 9, 9)),     # year Sheikh Safi al-Din died
    ],
)
def test_islamic_to_gregorian(ah_year: int, ah_month: int, ah_day: int, expected: tuple[int, int, int]) -> None:
    start, _ = convert(CalendarDate(Calendar.ISLAMIC_LUNAR, ah_year, ah_month, ah_day),
                       Calendar.GREGORIAN_PROLEPTIC)
    assert (start.year, start.month, start.day) == expected


@pytest.mark.parametrize(
    ("ah_year", "expected_span"),
    [(735, (1334, 1335)), (867, (1462, 1463)), (907, (1501, 1502)), (1445, (2023, 2024))],
)
def test_hijri_year_spans_two_gregorian_years(ah_year: int, expected_span: tuple[int, int]) -> None:
    """The core ADR-0005 claim: one Hijri year is never a single Gregorian year."""
    start, end = hijri_year_span(ah_year)
    assert (start.year, end.year) == expected_span
    assert to_gregorian_year_range(Calendar.ISLAMIC_LUNAR, ah_year) == expected_span


@pytest.mark.parametrize(
    ("jy", "jm", "jd", "expected"),
    [(1357, 1, 1, (1978, 3, 21)), (1400, 1, 1, (2021, 3, 21)), (1403, 1, 1, (2024, 3, 20))],
)
def test_persian_to_gregorian(jy: int, jm: int, jd: int, expected: tuple[int, int, int]) -> None:
    start, _ = convert(CalendarDate(Calendar.PERSIAN_SOLAR, jy, jm, jd), Calendar.GREGORIAN_PROLEPTIC)
    assert (start.year, start.month, start.day) == expected


def test_julian_gregorian_offset_grows_with_age() -> None:
    start_1500, _ = convert(CalendarDate(Calendar.JULIAN, 1500, 1, 1), Calendar.GREGORIAN_PROLEPTIC)
    assert (start_1500.year, start_1500.month, start_1500.day) == (1500, 1, 10)
    start_44bce, _ = convert(CalendarDate(Calendar.JULIAN, -43, 3, 15), Calendar.GREGORIAN_PROLEPTIC)
    assert (start_44bce.year, start_44bce.month, start_44bce.day) == (-43, 3, 13)


@pytest.mark.parametrize(("jy", "jm", "jd"), [(1400, 7, 15), (1357, 12, 29), (1399, 12, 30)])
def test_persian_roundtrip(jy: int, jm: int, jd: int) -> None:
    back = jdn_to_persian(persian_to_jdn(jy, jm, jd))
    assert (back.year, back.month, back.day) == (jy, jm, jd)


@pytest.mark.parametrize(("ah", "am", "ad"), [(907, 5, 17), (1445, 1, 1), (1, 1, 1)])
def test_islamic_roundtrip(ah: int, am: int, ad: int) -> None:
    back = jdn_to_islamic(islamic_to_jdn(ah, am, ad))
    assert (back.year, back.month, back.day) == (ah, am, ad)


def test_leap_rules() -> None:
    assert gregorian_leap(2000) and not gregorian_leap(1900)
    assert persian_leap(1399) is True          # Esfand 1399 had 30 days
    assert persian_leap(1400) is False
    assert month_length(Calendar.PERSIAN_SOLAR, 1399, 12) == 30
    assert month_length(Calendar.PERSIAN_SOLAR, 1400, 12) == 29
    assert month_length(Calendar.ISLAMIC_LUNAR, 907, 1) == 30
    assert month_length(Calendar.ISLAMIC_LUNAR, 907, 2) == 29


def test_unknown_calendar_is_refused() -> None:
    with pytest.raises(ValueError):
        convert(CalendarDate(Calendar.UNKNOWN, 1400), Calendar.GREGORIAN_PROLEPTIC)


@pytest.mark.parametrize("year", [1357, 1399, 1400, 1403])
@pytest.mark.parametrize("month", [1, 6, 7, 8, 11, 12])
def test_persian_roundtrip_every_month(year: int, month: int) -> None:
    """Regression: Esfand/Bahman round-trips (a month-aggregation off-by-one broke months 8-12)."""
    last = month_length(Calendar.PERSIAN_SOLAR, year, month)
    for day in (1, last):
        back = jdn_to_persian(persian_to_jdn(year, month, day))
        assert (back.year, back.month, back.day) == (year, month, day)


def test_persian_month_boundaries_line_up() -> None:
    assert persian_to_jdn(1400, 7, 1) - persian_to_jdn(1400, 6, 31) == 1
    assert persian_to_jdn(1400, 12, 1) - persian_to_jdn(1400, 11, 30) == 1
    assert persian_to_jdn(1401, 1, 1) - persian_to_jdn(1400, 12, 29) == 1
