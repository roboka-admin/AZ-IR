# ADR-0003 — گراف دانش assertion-محور و bitemporal

**Status:** Accepted · **Date:** 2026-09-12

## Context
دست‌نوشته هم `relationship tables` دارد (born_in, contains, …) هم `Claim → Evidence → Source`.
این دو یعنی هر فکت در دو جا ذخیره می‌شود و می‌توانند واگرا شوند. همچنین رابطه‌ها در زمان تغییر می‌کنند
(«تبریز پایتخت ایلخانان بود» فقط در ۱۲۶۵–۱۳۳۵ درست است) و پژوهشگر ممکن است بعداً نظرش عوض شود.

## Decision
### ۱) هر یال گراف یک Assertion است
```text
assertion(
  id, subject_type, subject_id, predicate, object_type, object_id,
  valid_from_year, valid_to_year,        -- نرمال‌شده (سال نجومی) برای index
  valid_calendar, valid_display, valid_precision,   -- آنچه منبع گفته
  confidence,                            -- high|medium|low
  status,                                -- proposed|accepted|rejected|disputed
  asserted_by, asserted_at, superseded_at, superseded_by, revision
)
evidence(id, assertion_id, source_id, locator_type, locator, quote_original, quote_translation, stance)
                                         -- stance: supports|contradicts|qualifies
```
پیش‌فرض: `predicate` از یک **واژگان کنترل‌شده** است (ADR-0012) با سطح و معکوس‌پذیری مشخص
(`born_in ⊂ associated_with`, `inverse(lived_in) = hosted`).

### ۲) ستون‌های روی entity یک projection از assertionها هستند
`person.birth_place_id` و امثال آن از assertionهای `status=accepted` با بیشترین confidence مشتق و
materialize می‌شوند (`EntityProjectionService`). **منبع حقیقت assertion store است.**
اگر دو assertion accepted متضاد وجود داشته باشد → projection مقدار ندارد و وضعیت `disputed` بالا می‌آید.

### ۳) Bitemporal
- **valid time**: وقتی در جهان واقعی درست بوده.
- **transaction time**: `asserted_at` / `superseded_at` (هیچ‌وقت فیزیکی حذف نمی‌شود؛ فقط supersede).
- همه‌ی writeها یک ردیف `audit_log` می‌سازند (actor, action, entity, before/after JSONB, request_id).

### ۴) اختلاف تاریخی پنهان نمی‌شود
API همیشه `disagreements` را برمی‌گرداند اگر assertion متضاد accepted/proposed داشته باشیم.
UI باید بتواند «روایت الف (منبع X) / روایت ب (منبع Y)» نشان دهد. (AGENTS.md #15 در دست‌نوشته)

## Consequences
- مثبت: provenance در سطح یال؛ قابلیت پاسخ به «چرا این را می‌گوییم؟»؛ export به RDF/Wikidata trivial؛
  رابطه‌های زمان‌دار بدون جدول جدید برای هر نوع رابطه.
- منفی: query گراف دو hop است → با projection و materialized rank جبران می‌شود.
- ریسک: اگر projection با assertion sync نباشد، داده‌ی متناقض می‌بینیم → **تست‌های invariant**
  (هر projection باید از assertionهای accepted مشتق شده باشد) در CI اجرا می‌شود.
