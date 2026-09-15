"""Temporal model: dual representation + interval algebra (ADR-0005).

Two representations are always kept:

* **faithful** -- what the source actually said (calendar, original text, precision);
* **normalized** -- a comparable interval in *astronomical* year numbering
  (year 0 = 1 BCE, year -1 = 2 BCE) used for indexing, ordering and overlap queries.

The golden rule: a normalized interval is *always* an interval, its width is derived from
``precision`` (never guessed ad-hoc), and an ``unknown`` date is never turned into a number
that pretends to be known.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .enums import Calendar, Confidence, Precision

#: Bounds used for open-ended precisions. They describe the *research horizon*, not a claim
#: about the world; anything outside them simply is not in scope of this atlas.
RESEARCH_FLOOR: Final[int] = -3000
RESEARCH_CEIL: Final[int] = 2100

#: ``circa`` fuzz in years. Deliberately a named constant so it can be tuned and audited.
CIRCA_FUZZ_YEARS: Final[int] = 10
QUARTER_CENTURY_FUZZ_YEARS: Final[int] = 12


class TemporalMode:
    AT = "at"
    DURING = "during"
    OVERLAPS = "overlaps"

    ALL: Final[tuple[str, ...]] = (AT, DURING, OVERLAPS)


def century_bounds(year: int) -> tuple[int, int]:
    """First/last astronomical year of the century containing ``year``.

    CE centuries are 1-100, 101-200, ...; BCE centuries are symmetric around year 0
    (which is 1 BCE): -100..-1 is the 1st century BCE, -200..-101 the 2nd, and so on.
    """
    if year > 0:
        start = ((year - 1) // 100) * 100 + 1
        return start, start + 99
    n = (-year + 100) // 100
    return -(n * 100), -((n - 1) * 100 + 1)


def floor_century(year: int) -> int:
    return century_bounds(year)[0]


def ceil_century(year: int) -> int:
    return century_bounds(year)[1]


def century_number(year: int) -> int:
    """Human century ordinal, e.g. ``century_number(1450) == 15``, ``century_number(-550) == 6`` (BCE)."""
    if year > 0:
        return (year - 1) // 100 + 1
    return (-year + 100) // 100


def bce_to_astronomical(bce_year: int) -> int:
    """``550 BCE -> -549`` (astronomical year numbering, year 0 = 1 BCE)."""
    return 1 - bce_year


def floor_decade(year: int) -> int:
    return (year // 10) * 10


def floor_millennium(year: int) -> int:
    return ((year - 1) // 1000) * 1000 + 1 if year > 0 else year


@dataclass(frozen=True, slots=True)
class TemporalInterval:
    """A normalized, comparable interval plus the faithful source representation."""

    year_from: int
    year_to: int
    precision: Precision = Precision.UNKNOWN
    calendar: Calendar = Calendar.GREGORIAN_PROLEPTIC
    confidence: Confidence = Confidence.MEDIUM
    display_from: str | None = None
    display_to: str | None = None
    display: str | None = None

    def __post_init__(self) -> None:
        if self.year_from > self.year_to:
            raise ValueError(
                f"invariant violated: year_from ({self.year_from}) > year_to ({self.year_to})"
            )
        if self.precision is Precision.UNKNOWN and self.confidence not in (
            Confidence.LOW,
            Confidence.DISPUTED,
        ):
            # An unknown date must never look confident (data lint rule D8).
            object.__setattr__(self, "confidence", Confidence.LOW)

    # ------------------------------------------------------------------ algebra

    @property
    def midpoint(self) -> float:
        return (self.year_from + self.year_to) / 2

    @property
    def span(self) -> int:
        return self.year_to - self.year_from

    @property
    def is_open_ended(self) -> bool:
        return self.year_from <= RESEARCH_FLOOR or self.year_to >= RESEARCH_CEIL

    def overlaps(self, other: TemporalInterval) -> bool:
        return self.year_from <= other.year_to and other.year_from <= self.year_to

    def contains(self, other: TemporalInterval) -> bool:
        return self.year_from <= other.year_from and other.year_to <= self.year_to

    def contains_year(self, year: int) -> bool:
        return self.year_from <= year <= self.year_to

    def intersection(self, other: TemporalInterval) -> TemporalInterval | None:
        start, end = max(self.year_from, other.year_from), min(self.year_to, other.year_to)
        if start > end:
            return None
        return TemporalInterval(start, end, Precision.RANGE, self.calendar, self.confidence)

    def distance_to_year(self, year: int) -> int:
        if self.contains_year(year):
            return 0
        return self.year_from - year if year < self.year_from else year - self.year_to

    def matches(self, window: TimeWindow) -> bool:
        """The single place where time filtering is decided (used by both drivers)."""
        if window.mode == TemporalMode.DURING:
            return window.contains(self)
        if window.mode == TemporalMode.OVERLAPS:
            return window.overlaps(self)
        return window.overlaps(self)

    # ------------------------------------------------------------------ display

    def display_text(self, locale: str = "fa") -> str:
        if self.display:
            return self.display
        return format_interval(self, locale)

    @classmethod
    def unknown(cls, note: str | None = None) -> TemporalInterval:
        return cls(
            RESEARCH_FLOOR,
            RESEARCH_CEIL,
            Precision.UNKNOWN,
            Calendar.UNKNOWN,
            Confidence.LOW,
            display=note,
        )


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A time query: never a bare instant (ADR-0005 / docs 05 §4)."""

    year_from: int
    year_to: int
    mode: str = TemporalMode.AT
    calendar: Calendar = Calendar.GREGORIAN_PROLEPTIC

    def __post_init__(self) -> None:
        if self.mode not in TemporalMode.ALL:
            raise ValueError(f"unsupported temporal mode: {self.mode!r}")
        if self.year_from > self.year_to:
            raise ValueError("window.year_from must be <= window.year_to")

    @classmethod
    def at(cls, year: int, calendar: Calendar = Calendar.GREGORIAN_PROLEPTIC) -> TimeWindow:
        return cls(year, year, TemporalMode.AT, calendar)

    @classmethod
    def span(
        cls,
        year_from: int,
        year_to: int,
        mode: str = TemporalMode.OVERLAPS,
        calendar: Calendar = Calendar.GREGORIAN_PROLEPTIC,
    ) -> TimeWindow:
        return cls(year_from, year_to, mode, calendar)

    def overlaps(self, interval: TemporalInterval) -> bool:
        return interval.overlaps(self.as_interval())

    def contains(self, interval: TemporalInterval) -> bool:
        return self.as_interval().contains(interval)

    def as_interval(self) -> TemporalInterval:
        return TemporalInterval(self.year_from, self.year_to, Precision.RANGE, self.calendar)

    @property
    def representative_year(self) -> int:
        """The year used to pick time-varying geometry and names.

        Open-ended windows (``BEFORE``/``AFTER``) carry research sentinels, so their midpoint is
        meaningless; fall back to the bounded end instead (ADR-0005).
        """
        if self.year_from == self.year_to:
            return self.year_from
        if self.year_from <= RESEARCH_FLOOR:
            return self.year_to
        if self.year_to >= RESEARCH_CEIL:
            return self.year_from
        return (self.year_from + self.year_to) // 2

    def bucket(self, size: int) -> TimeWindow:
        """Cache-friendly rounding of the window (ADR-0009 / docs 08 §4)."""
        if size <= 1:
            return self
        return TimeWindow(
            (self.year_from // size) * size,
            ((self.year_to // size) + 1) * size - 1,
            self.mode,
            self.calendar,
        )


# ---------------------------------------------------------------------- fuzz table

_FUZZ: Final[dict[Precision, tuple[int, int]]] = {
    Precision.EXACT_DAY: (0, 0),
    Precision.EXACT_MONTH: (0, 0),
    Precision.EXACT_YEAR: (0, 0),
    Precision.CIRCA_YEAR: (-CIRCA_FUZZ_YEARS, CIRCA_FUZZ_YEARS),
    Precision.QUARTER_CENTURY: (-QUARTER_CENTURY_FUZZ_YEARS, QUARTER_CENTURY_FUZZ_YEARS),
}


def derive_interval(
    *,
    year: int,
    year_to: int | None = None,
    precision: Precision,
    calendar: Calendar = Calendar.GREGORIAN_PROLEPTIC,
    confidence: Confidence = Confidence.MEDIUM,
) -> TemporalInterval:
    """Build a normalized interval from a faithful date declaration.

    This is the *only* function allowed to widen a date into a range, and it does so purely
    from ``precision`` (docs/03-temporal-and-calendar.md §2).
    """
    if precision in _FUZZ:
        low, high = _FUZZ[precision]
        return TemporalInterval(year + low, (year_to or year) + high, precision, calendar, confidence)

    if precision is Precision.DECADE:
        return TemporalInterval(floor_decade(year), floor_decade(year) + 9, precision, calendar, confidence)
    if precision is Precision.CENTURY:
        return TemporalInterval(floor_century(year), ceil_century(year), precision, calendar, confidence)
    if precision is Precision.HALF_MILLENNIUM:
        base = floor_millennium(year)
        half = 0 if ((year - base) < 500) else 500
        return TemporalInterval(base + half, base + half + 499, precision, calendar, confidence)
    if precision is Precision.MILLENNIUM:
        base = floor_millennium(year)
        return TemporalInterval(base, base + 999, precision, calendar, confidence)
    if precision is Precision.BEFORE:
        return TemporalInterval(RESEARCH_FLOOR, year, precision, calendar, confidence)
    if precision is Precision.AFTER:
        return TemporalInterval(year, RESEARCH_CEIL, precision, calendar, confidence)
    if precision is Precision.RANGE:
        if year_to is None:
            raise ValueError("precision=range requires year_to")
        return TemporalInterval(year, year_to, precision, calendar, confidence)
    return TemporalInterval.unknown()


# ---------------------------------------------------------------------- formatting

def _num(value: int, locale: str) -> str:
    """Persian digits for ``fa``, Latin for everything else (docs 03 §6)."""
    if locale == "fa":
        return str(value).translate(str.maketrans("0123456789-", "۰۱۲۳۴۵۶۷۸۹−"))
    return str(value)


def format_year(year: int, locale: str = "fa") -> str:
    if year == RESEARCH_FLOOR:
        return "نامعلوم" if locale == "fa" else "unknown"
    if year == RESEARCH_CEIL:
        return "نامعلوم" if locale == "fa" else "unknown"
    if year < 0:
        return f"{_num(-year + 1, locale)} {'ق.م' if locale == 'fa' else 'BCE'}"
    if year == 0:
        return f"{_num(1, locale)} {'ق.م' if locale == 'fa' else 'BCE'}"
    return f"{_num(year, locale)} {'م' if locale == 'fa' else 'CE'}"


def format_interval(interval: TemporalInterval, locale: str = "fa") -> str:
    p = interval.precision
    if p is Precision.UNKNOWN:
        return interval.display or ("تاریخ نامعلوم" if locale == "fa" else "date unknown")
    if p is Precision.CIRCA_YEAR:
        return (
            f"حدود {_num(interval.year_from + CIRCA_FUZZ_YEARS, locale)} م"
            if locale == "fa"
            else f"c. {_num(interval.year_from + CIRCA_FUZZ_YEARS, locale)} CE"
        )
    if p is Precision.CENTURY:
        n = century_number(interval.year_from)
        bce = interval.year_to <= 0
        if locale == "fa":
            return f"سدهٔ {_num(n, locale)} {'ق.م' if bce else 'م'}"
        return f"{_ordinal(n)} century {'BCE' if bce else 'CE'}"
    if p is Precision.BEFORE:
        return f"پیش از {_num(interval.year_to, locale)} م" if locale == "fa" else (
            f"before {_num(interval.year_to, locale)} CE"
        )
    if p is Precision.AFTER:
        return f"پس از {_num(interval.year_from, locale)} م" if locale == "fa" else (
            f"after {_num(interval.year_from, locale)} CE"
        )
    if p is Precision.DECADE:
        return f"دههٔ {_num(interval.year_from, locale)} م" if locale == "fa" else (
            f"{_num(interval.year_from, locale)}s CE"
        )
    if interval.year_from == interval.year_to:
        return format_year(interval.year_from, locale)
    return f"{_num(interval.year_from, locale)}–{_num(interval.year_to, locale)}" + (
        " م" if locale == "fa" else " CE"
    )


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
