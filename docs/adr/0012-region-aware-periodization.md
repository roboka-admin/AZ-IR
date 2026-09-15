# ADR-0012 — دوره‌بندی منطقه‌آگاه، چندطرحی، و واژگان کنترل‌شده

**Status:** Accepted · **Date:** 2026-09-12

## Context
«دوره‌ی صفوی» یک برچسب ساده نیست: در آذربایجان معنای سیاسی/مذهبی/اقتصادی متفاوتی دارد و
تاریخ شروع و پایان آن هم می‌تواند محل بحث باشد (۱۵۰۱ تاج‌گذاری در تبریز؟ یا ۱۴۹۹؟ یا سقوط ۱۷۲۲/۱۷۳۶؟).
همچنین چند طرح دوره‌بندی مشروع وجود دارد (دودمانی، باستان‌شناختی، اقتصادی).

## Decision
```text
taxonomy(id, code, kind, lang, label, parent_id, source_id, note)
  kind ∈ place_kind | event_kind | predicate | role | period | theme | source_kind | locator_type

period_scheme(id, name, description, owner, source_id, is_default)
period(id, scheme_id, code, label_fa, label_en, parent_id,
       applies_to_place_id,     -- NULL = کل حوزه‌ی مطالعه
       year_from, year_to, precision, confidence, source_id)
```
قواعد:
1. membership یک موجودیت در period **اول از تاریخش مشتق** می‌شود (derived)؛ tag صریح فقط وقتی مجاز است
   که تاریخ نامعلوم باشد یا منبع صریحاً دوره‌بندی دیگری بدهد — و در آن صورت tag هم یک `assertion` است.
2. هر period باید `source_id` داشته باشد (خودِ دوره‌بندی یک انتخاب علمی است).
3. UI همیشه بگوید از کدام scheme استفاده می‌کند و امکان سوییچ دادن بدهد.

### واژگان کنترل‌شده
همه‌ی `kind`ها و `predicate`ها از `taxonomy` می‌آیند، نه از string آزاد. افزودن مقدار جدید =
یک migration داده‌ای + review. این جلوی «مسجد / مسجدجامع / Mosque / مسجد جامع» به‌عنوان چهار نوع را می‌گیرد.

## Consequences
- مثبت: سازگاری داده، فیلتر درست در timeline، امکان مقایسه‌ی schemeها.
- منفی: انعطاف کمتر برای editor → با فرایند «پیشنهاد taxonomy» جبران می‌شود.
