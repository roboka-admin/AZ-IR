# مدل زمانی و موتور تقویم

> مرجع: ADR-0005 (calendar), ADR-0003 (bitemporal)
> پیاده‌سازی: `backend/src/azir/domain/temporal.py`, `backend/src/azir/domain/calendar.py`

## ۱. انواع پایه

```python
class Calendar(StrEnum): GREGORIAN_PROLEPTIC | JULIAN | ISLAMIC_LUNAR | PERSIAN_SOLAR | UNKNOWN
class Precision(StrEnum):
    EXACT_DAY | EXACT_MONTH | EXACT_YEAR | CIRCA_YEAR | DECADE | QUARTER_CENTURY
    | CENTURY | HALF_MILLENNIUM | MILLENNIUM | BEFORE | AFTER | RANGE | UNKNOWN
class Confidence(StrEnum): HIGH | MEDIUM | LOW | DISPUTED

@dataclass(frozen=True)
class TemporalPoint:      # یک لحظه با بازنمایی دوگانه
    calendar: Calendar
    year: int             # در تقویم مبدأ
    month: int | None
    day: int | None
    precision: Precision
    original_text: str    # آنچه منبع نوشته: "735 AH", "circa 1400", "8th century"

@dataclass(frozen=True)
class TemporalInterval:   # همیشه یک بازه است، حتی برای exact_day
    year_from: int        # سال نجومی (0 = 1 BCE, -1 = 2 BCE)
    year_to: int
    precision: Precision
    calendar: Calendar
    display_from: str | None
    display_to: str | None
```

## ۲. قاعده‌ی مشتق‌سازی fuzz از precision (تنها جای مجاز برای «حدس»)

| precision | بازه‌ی نرمال‌شده |
|-----------|------------------|
| `exact_day/month/year` | `[y, y]` |
| `circa_year` | `[y-10, y+10]` (ثابت قابل تنظیم: `TEMPORAL_CIRCA_FUZZ`) |
| `decade` | `[floor10(y), floor10(y)+9]` |
| `quarter_century` | `[y-12, y+12]` |
| `century` | `[floor100(y)+1, floor100(y)+100]` (قرن ۸ = ۷۰۱–۸۰۰ میلادی؛ قرن ۸ قمری جدا تبدیل می‌شود) |
| `half_millennium` | نیمه‌ی مربوطه از هزاره |
| `millennium` | `[floor1000(y)+1, +1000]` |
| `before` | `[RESEARCH_FLOOR(-3000), y]` |
| `after` | `[y, RESEARCH_CEIL(2100)]` |
| `range` | `[y_from, y_to]` که خود منبع داده |
| `unknown` | `[RESEARCH_FLOOR, RESEARCH_CEIL]` + `confidence=low` اجباری |

**قانون:** هر مقدار `unknown` باید در UI با برچسب «تاریخ نامعلوم» بیاید، نه با یک نقطه روی تایم‌لاین.

## ۳. تبدیل تقویم‌ها (توابع خالص)

```python
def to_gregorian_range(cal: Calendar, y: int, m: int|None, d: int|None,
                       precision: Precision) -> tuple[int, int, int|None, int|None]
def from_gregorian(cal: Calendar, gy: int, gm: int, gd: int) -> CalendarDate
def hijri_to_gregorian_span(ah_year: int) -> tuple[date, date]      # یک سال قمری → بازه‌ی میلادی
def persian_to_gregorian(jy: int, jm: int, jd: int) -> date         # الگوریتم ۳۳ساله (Borkowski/Ahmad)
def julian_to_gregorian(y: int, m: int, d: int) -> date
```

خطای مجاز و مستند:
- `islamic_lunar → gregorian`: **±۱ روز** (تبدیل جداولی/حسابی، نه رؤیت هلال). در UI: «۱۷ رجب ۹۰۷ ≈ ۲۷ ژوئیه ۱۵۰۱».
- `persian_solar → gregorian`: دقیق برای ۱۲۰۰–۱۵۰۰ ش؛ خارج از آن ±۱ روز مستند می‌شود.
- `julian ↔ gregorian`: دقیق (cutover 1582-10-15).

تست‌های طلایی (golden) — این مقادیر با اجرای واقعی `domain/calendar.py` تأیید شده‌اند و در
`backend/tests/domain/test_calendar.py` قفل شده‌اند:

| ورودی | خروجی مورد انتظار |
|-------|-------------------|
| ۱ محرم ۱۴۴۵ قمری | ۲۰۲۳-۰۷-۱۹ میلادی |
| ۱ محرم ۹۰۷ قمری | ۱۵۰۱-۰۷-۲۷ میلادی (نزدیک فتح تبریز توسط شاه‌اسماعیل) |
| سال ۷۳۵ قمری | ۱۳۳۴-۰۹-۰۹ .. ۱۳۳۵-۰۸-۲۸ میلادی |
| سال ۸۶۷ قمری | ۱۴۶۲-۱۰-۰۵ .. ۱۴۶۳-۰۹-۲۳ میلادی |
| ۱ فروردین ۱۳۵۷ ش | ۱۹۷۸-۰۳-۲۱ میلادی |
| ۱ فروردین ۱۴۰۰ ش | ۲۰۲۱-۰۳-۲۱ میلادی |
| ۱ ژانویه ۱۵۰۰ جولیان | ۱۵۰۰-۰۱-۱۰ گرگورین |
| ۱۵ مارس ۴۴ ق.م جولیان (ایدهای مارس) | ۴۴-۰۳-۱۳ گرگورین proleptic = سال نجومی −۴۳ |
| سال کبیسه ۱۳۹۹ ش | اسفند ۳۰ روزه |

## ۴. جبر بازه‌ها (interval algebra) — تنها جای مجاز برای مقایسه‌ی زمانی

```python
overlaps(a, b) -> bool        # a.start <= b.end and b.start <= a.end
contains(a, b) -> bool        # a.start <= b.start and b.end <= a.end
at(year, iv) -> bool          # overlaps(Interval(year, year), iv)
intersects_window(iv, win, mode) -> bool    # mode ∈ at|during|overlaps
distance_years(a, b) -> int   # برای مرتب‌سازی نزدیک‌ترین به زمان فعال
```

`mode` در API:
- `at` (پیش‌فرض): چه چیزی در این لحظه معتبر بود؟ → `overlaps(point, iv)`
- `during`: چه چیزی کاملاً داخل این پنجره است؟ → `contains(window, iv)`
- `overlaps`: هر هم‌پوشانی → برای بازه‌های پژوهشی

## ۵. Bitemporal

```text
valid time      : year_from/year_to/precision/calendar  → چه وقتی در جهان درست بود
transaction time: asserted_at / superseded_at / revision → چه وقتی ما این را ثبت/اصلاح کردیم
```

Query «تاریخ در زمان t» فقط با valid time کار می‌کند.
Query «چه چیزی در تاریخ R منتشر بود» با transaction time (برای مقایسه‌ی نسخه‌ها و بازبینی).

## ۶. نمایش در UI (fa-first)

| داده | نمایش فارسی | نمایش انگلیسی |
|------|-------------|----------------|
| ۷۳۵ قمری / ۱۳۳۴–۳۵ میلادی | «۷۳۵ ق ≈ ۱۳۳۴–۱۳۳۵ م» | «735 AH ≈ 1334–1335 CE» |
| circa 1400 | «حدود ۱۴۰۰ م» | «c. 1400 CE» |
| century 8 (Hijri) | «سدهٔ ۸ ق» | «8th century AH» |
| before 1200 | «پیش از ۱۲۰۰ م» | «before 1200 CE» |
| unknown | «تاریخ نامعلوم» | «date unknown» |
| BCE | «۵۵۰ ق.م» | «550 BCE» |

سیاست ارقام: پیش‌فرض **رقم فارسی** در locale `fa` و لاتین در `en`؛ این یک تابع واحد
`formatNumber(n, locale)` است نه تصمیم هر کامپوننت.
