# ADR-0006 — مدل نام چندزبانه و چندنوشتاری

**Status:** Accepted · **Date:** 2026-09-12

## Context
موجودیت‌های ما نام‌های چندگانه دارند: فارسی «تبریز»، عربی/لاتین «Tabrīz»، سریانی/ارمنی «Tawrēz»،
ترکی آذربایجانی لاتین «Təbriz»، لاتین اروپایی «Tauris»، و نام‌های تاریخی متفاوت در دوره‌های مختلف
(«رضائیه» برای ارومیه بین ۱۳۵۸–۱۳۸۸ ش). نام یک ستون نیست.

## Decision
```text
name_variant(
  id, entity_type, entity_id,
  lang,              -- BCP-47: fa, en, azb-Latn, azb-Arab, ar, hy, ka, ru, ota, fr, la ...
  script,            -- Arab|Latn|Armn|Geor|Cyrl
  form,              -- خودِ رشته
  form_normalized,   -- نرمال‌شده برای search (ADR-0007)
  transliteration_system,  -- EI3 | DMG | Common | BGN | None
  kind,              -- preferred | official | historical | alternative | endonym | exonym | abbreviation
  is_preferred_for_locale, -- نمایش پیش‌فرض برای یک locale
  valid_from, valid_to,    -- وقتی این نام به‌کار می‌رفته
  source_id, note
)
```
قواعد resolve (تابع خالص `resolve_display_name(entity, locale, at_time)`):
1. نام `preferred` برای locale کاربر که در `at_time` معتبر باشد.
2. اگر نبود → `preferred` در هر زمانی برای آن locale.
3. اگر نبود → fallback زنجیره‌ای locale (`fa` → `fa-Latn`? نه؛ `en` → نام اصلی با script اصلی).
4. همیشه `alt_names` در پاسخ API برمی‌گردد تا UI «نام‌های دیگر» نشان دهد (و search پوشش یابد).

**سیاست transliteration:** تا تصمیم پژوهشی (سؤال باز #2 در design review)، پیش‌فرض `Common`
(رایج‌ترین شکل لاتین) و ثبت `transliteration_system` اجباری است تا بعداً بتوان bulk-migrate کرد.

## Consequences
- مثبت: search چندزبانه، نمایش تاریخی نام‌ها («در سال ۱۹۳۵ این شهر رضائیه نام داشت») بدون duplicate داده.
- منفی: هر write باید نام را در `name_variant` بنویسد نه روی entity (entity فقط `display_name_cache` دارد).
- الزام: `search_text` هر entity شامل همه‌ی name_variantهای نرمال‌شده است.
