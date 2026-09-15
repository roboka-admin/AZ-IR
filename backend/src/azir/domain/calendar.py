"""Calendar engine (ADR-0005).

Pure, deterministic conversions between the calendars our sources actually use:

* Gregorian (proleptic) -- the normalized/query representation
* Julian               -- sources before the 1582-10-15 cutover
* Islamic lunar (tabular/civil, Friday epoch 16 July 622 CE Julian)
* Persian solar (arithmetic 2820-year cycle, ``Calendrical Calculations``/Fourmilab)

Everything is routed through a Julian Day Number (JDN) so the algorithms stay small and
verifiable. Known accuracy:

* islamic_lunar -> gregorian: **±1 day** (arithmetic calendar, not crescent observation);
  a whole Hijri year maps to a *range* of Gregorian dates, never to a single year.
* persian_solar  -> gregorian: exact for the algorithmic calendar; the observed (astronomical)
  Nowruz may differ by one day in rare years -- documented, not silently fixed.
* julian <-> gregorian: exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor

from .enums import Calendar

ISLAMIC_EPOCH_JDN = 1_948_440  # 1 Muharram 1 AH == 16 July 622 (Julian) == 19 July 622 (Gregorian)
PERSIAN_EPOCH_JDN = 1_948_320  # 1 Farvardin 1 AP == 19 March 622 (Julian)
# The Julian -> Gregorian cutover (15 Oct 1582) is handled implicitly: dates before it are
# stored as Julian by the editor and converted through JDN, dates after it as Gregorian.


@dataclass(frozen=True, slots=True)
class CalendarDate:
    calendar: Calendar
    year: int
    month: int | None = None
    day: int | None = None

    def __str__(self) -> str:  # pragma: no cover - convenience only
        parts = [str(self.year)] + [f"{p:02d}" for p in (self.month, self.day) if p is not None]
        return "-".join(parts)


# --------------------------------------------------------------------- JDN helpers


def gregorian_to_jdn(year: int, month: int, day: int) -> int:
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32_045


def jdn_to_gregorian(jdn: int) -> CalendarDate:
    a = jdn + 32_044
    b = (4 * a + 3) // 146_097
    c = a - (146_097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + m // 10
    return CalendarDate(Calendar.GREGORIAN_PROLEPTIC, year, month, day)


def julian_to_jdn(year: int, month: int, day: int) -> int:
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - 32_083


def jdn_to_julian(jdn: int) -> CalendarDate:
    c = jdn + 32_082
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = d - 4800 + m // 10
    return CalendarDate(Calendar.JULIAN, year, month, day)


def islamic_to_jdn(year: int, month: int, day: int) -> int:
    """Tabular (civil) Islamic calendar -> JDN."""
    return (
        day
        + ceil(29.5 * (month - 1))
        + (year - 1) * 354
        + floor((3 + 11 * year) / 30)
        + ISLAMIC_EPOCH_JDN
        - 1
    )


def jdn_to_islamic(jdn: int) -> CalendarDate:
    year = floor((30 * (jdn - ISLAMIC_EPOCH_JDN) + 10_646) / 10_631)
    month = min(12, ceil((jdn - (29 + islamic_to_jdn(year, 1, 1))) / 29.5) + 1)
    if month < 1:
        month = 1
    day = jdn - islamic_to_jdn(year, month, 1) + 1
    return CalendarDate(Calendar.ISLAMIC_LUNAR, year, month, day)


def persian_to_jdn(year: int, month: int, day: int) -> int:
    """Arithmetic Persian (Solar Hijri) calendar -> JDN."""
    epbase = year - (474 if year >= 0 else 473)
    epyear = 474 + epbase % 2820
    # Farvardin..Shahrivar are 31 days, Mehr..Bahman 30, Esfand 29/30.
    agg = (month - 1) * 31 if month <= 7 else (month - 7) * 30 + 186
    return (
        day
        + agg
        + floor((epyear * 682 - 110) / 2816)
        + (epyear - 1) * 365
        + floor(epbase / 2820) * 1_029_983
        + PERSIAN_EPOCH_JDN
    )


def jdn_to_persian(jdn: int) -> CalendarDate:
    depoch = jdn - persian_to_jdn(475, 1, 1)
    cycle = floor(depoch / 1_029_983)
    cyear = depoch % 1_029_983
    if cyear == 1_029_982:
        ycycle = 2820
    else:
        aux1 = floor(cyear / 366)
        aux2 = cyear % 366
        ycycle = floor((2134 * aux1 + 2816 * aux2 + 2815) / 1_028_522) + aux1 + 1
    year = ycycle + 2820 * cycle + 474
    if year <= 0:
        year -= 1
    yday = jdn - persian_to_jdn(year, 1, 1) + 1
    month = ceil(yday / 31) if yday <= 186 else ceil((yday - 6) / 30)
    day = jdn - persian_to_jdn(year, month, 1) + 1
    return CalendarDate(Calendar.PERSIAN_SOLAR, year, month, day)


def _to_jdn(calendar: Calendar, year: int, month: int | None, day: int | None) -> int:
    m, d = month or 1, day or 1
    if calendar is Calendar.GREGORIAN_PROLEPTIC:
        return gregorian_to_jdn(year, m, d)
    if calendar is Calendar.JULIAN:
        return julian_to_jdn(year, m, d)
    if calendar is Calendar.ISLAMIC_LUNAR:
        return islamic_to_jdn(year, m, d)
    if calendar is Calendar.PERSIAN_SOLAR:
        return persian_to_jdn(year, m, d)
    raise ValueError(f"cannot convert unknown calendar for year {year}")


def _from_jdn(calendar: Calendar, jdn: int) -> CalendarDate:
    if calendar is Calendar.GREGORIAN_PROLEPTIC:
        return jdn_to_gregorian(jdn)
    if calendar is Calendar.JULIAN:
        return jdn_to_julian(jdn)
    if calendar is Calendar.ISLAMIC_LUNAR:
        return jdn_to_islamic(jdn)
    if calendar is Calendar.PERSIAN_SOLAR:
        return jdn_to_persian(jdn)
    raise ValueError("cannot convert to unknown calendar")


# --------------------------------------------------------------------- public API


def convert(
    value: CalendarDate, target: Calendar
) -> tuple[CalendarDate, CalendarDate]:
    """Convert one date into ``target`` returning a (start, end) span.

    The result is a *span* on purpose: converting a coarse Islamic date (year only) into the
    Gregorian calendar produces a range, and pretending otherwise would fabricate precision
    (AGENTS.md rule 6).
    """
    if value.calendar is Calendar.UNKNOWN or target is Calendar.UNKNOWN:
        raise ValueError("unknown calendar cannot be converted")
    start_jdn = _to_jdn(value.calendar, value.year, value.month, value.day)
    if value.month is None:
        end_jdn = _to_jdn(value.calendar, value.year, 12, 1) + month_length(value.calendar, value.year, 12) - 1
    elif value.day is None:
        end_jdn = start_jdn + month_length(value.calendar, value.year, value.month) - 1
    else:
        end_jdn = start_jdn
    return _from_jdn(target, start_jdn), _from_jdn(target, end_jdn)


def month_length(calendar: Calendar, year: int, month: int) -> int:
    if calendar is Calendar.ISLAMIC_LUNAR:
        return 30 if month % 2 == 1 else 29
    if calendar is Calendar.PERSIAN_SOLAR:
        if month <= 6:
            return 31
        if month <= 11:
            return 30
        return 30 if persian_leap(year) else 29
    if calendar is Calendar.GREGORIAN_PROLEPTIC:
        start = gregorian_to_jdn(year, month, 1)
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        return gregorian_to_jdn(ny, nm, 1) - start
    if calendar is Calendar.JULIAN:
        start = julian_to_jdn(year, month, 1)
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        return julian_to_jdn(ny, nm, 1) - start
    raise ValueError("unknown calendar")


def persian_leap(year: int) -> bool:
    """Leap test derived from the arithmetic calendar itself (366-day year)."""
    return persian_to_jdn(year + 1, 1, 1) - persian_to_jdn(year, 1, 1) == 366


def gregorian_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def hijri_year_span(ah_year: int) -> tuple[CalendarDate, CalendarDate]:
    """A whole Hijri year as a Gregorian range -- the common ``867 AH -> 1462-1463`` case."""
    return convert(CalendarDate(Calendar.ISLAMIC_LUNAR, ah_year), Calendar.GREGORIAN_PROLEPTIC)


def to_gregorian_year_range(
    calendar: Calendar, year: int, month: int | None = None, day: int | None = None
) -> tuple[int, int]:
    """Normalized year range used for indexing/ordering (astronomical year numbering)."""
    if calendar is Calendar.UNKNOWN:
        raise ValueError("unknown calendar")
    start, end = convert(CalendarDate(calendar, year, month, day), Calendar.GREGORIAN_PROLEPTIC)
    return start.year, end.year


def from_gregorian_year(calendar: Calendar, gregorian_year: int) -> CalendarDate:
    """Approximate inverse: mid-point of the Gregorian year expressed in ``calendar``."""
    mid = (gregorian_to_jdn(gregorian_year, 1, 1) + gregorian_to_jdn(gregorian_year, 12, 31)) // 2
    return _from_jdn(calendar, mid)


def display(calendar: Calendar, year: int, month: int | None = None, day: int | None = None) -> str:
    """Faithful human label for a date in its own calendar (what the source said)."""
    suffix = {
        Calendar.ISLAMIC_LUNAR: " AH",
        Calendar.PERSIAN_SOLAR: " SH",
        Calendar.JULIAN: " (Julian)",
        Calendar.GREGORIAN_PROLEPTIC: " CE",
        Calendar.UNKNOWN: "",
    }[calendar]
    if calendar is Calendar.GREGORIAN_PROLEPTIC and year < 0:
        return f"{1 - year} BCE"
    if calendar is Calendar.GREGORIAN_PROLEPTIC and year == 0:
        return "1 BCE"
    parts = [str(year)] + [f"{p:02d}" for p in (month, day) if p is not None]
    return "-".join(parts) + suffix
