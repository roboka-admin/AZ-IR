# ADR-0005 — موتور تقویم و بازنمایی دوگانه‌ی زمان

**Status:** Accepted · **Date:** 2026-09-12

## Context
منابع ما با تقویم‌های گوناگون‌اند: هجری قمری (رایج‌ترین در منابع اسلامی)، هجری شمسی (اسناد معاصر)،
جولیان (منابع پیش از ۱۵۸۲)، گرگورین، و گاهی تقویم حیوانی ترک-مغول یا سال‌های جلوس.
سال قمری ≈ ۳۵۴٫۳۷ روز است، پس یک سال قمری معمولاً روی **دو** سال میلادی می‌افتد.
ذخیره‌ی `year = 1400` برای «circa 1400» یا برای «۸۶۷ قمری» = جعل دقت/جعل واقعیت (نقض AGENTS.md #6/#10).

## Decision
### ۱) بازنمایی دوگانه
هر کمیت زمانی دو بخش دارد:

```text
faithful  : { calendar, original_text, year, month, day, precision, era_note }
normalized: { year_from, year_to, month_from?, month_to? }   -- سال نجومی (year 0 = 1 BCE)
```

`precision ∈ exact_day | exact_month | exact_year | circa_year | decade | quarter_century
            | century | half_millennium | millennium | before | after | range | unknown`

**قاعده‌ی طلایی:** `normalized` همیشه یک **بازه** است (حتی برای exact_day، بازه‌ی یک‌روزه)،
و عرض بازه از `precision` مشتق می‌شود، نه از حدس:

| precision | fuzz |
|-----------|------|
| exact_year | ±0 |
| circa_year | ±10 (قابل تنظیم، در source مستند می‌شود) |
| decade | تا انتهای دهه |
| century | تا انتهای قرن |
| before | (-∞, year] |
| after | [year, +∞) |
| unknown | کل دامنه‌ی پژوهش (معمولاً 800 BCE تا امروز) |

### ۲) تقویم‌ها و تبدیل
- `calendar ∈ gregorian_proleptic | julian | islamic_lunar | persian_solar | unknown_calendar`
- تبدیل **تابع خالص و نسخه‌دار** است (`azir.domain.calendar`)، با جدول‌های تست طلایی (golden tests):
  - islamic_lunar → gregorian: یک **بازه** برمی‌گرداند (نه یک تاریخ)، با الگوریتم تبدیل جداولی + خطای ±۱ روز.
  - julian ↔ gregorian: cutover در ۱۵۸۲-۱۰-۱۵، قبل از آن grégoire proleptic با اختلاف محاسبه می‌شود.
  - persian_solar: الگوریتم ۳۳‌ساله (بیرشکی) با مستندسازی خطا.
- **هرگز** تقویم مبدأ را دور نمی‌ریزیم: `original_text` و `calendar` همیشه ذخیره می‌شوند تا کاربر بتواند
  «۷۳۵ قمری» را همان‌طور که منبع گفته ببیند.

### ۳) API: زمان یک instant نیست
```text
?t=1450&mode=at                     → همه‌چیزهایی که در ۱۴۵۰ معتبر بوده‌اند
?from=1400&to=1500&mode=overlaps    → هرچه با پنجره هم‌پوشانی دارد
?cal=AH&t=867                       → ورودی با تقویم هجری قمری
```
`mode ∈ at | during | overlaps` (پیش‌فرض `at`).

### ۴) Bitemporal
`valid_*` (جهان واقعی) جدا از `asserted_at/superseded_at` (دانش ما) — ADR-0003.

## Consequences
- مثبت: ترتیب‌دهی و مقایسه‌ی رویدادها درست می‌شود؛ نمایش «۷۳۵ ق / ۱۳۳۴–۳۵ م» ممکن است؛ هیچ دقتی جعل نمی‌شود.
- منفی: همه‌ی queryها باید با range کار کنند (GiST روی `int4range` یا دو ستون year)؛ UI باید عدم‌قطعیت را نشان دهد.
- الزام تست: property-based tests برای conversion (رفت‌وبرگشت)، golden tables برای تاریخ‌های شناخته‌شده
  (مثلاً ۱ محرم ۹۰۷ = ۱۵۰۱-۰۷-۱۷ ± ۱ روز، فتح تبریز توسط شاه‌اسماعیل).
