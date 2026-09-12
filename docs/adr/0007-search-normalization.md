# ADR-0007 — نرمال‌سازی و جست‌وجو برای فارسی/عربی

**Status:** Accepted · **Date:** 2026-09-12

## Context
`to_tsvector('english', …)` برای فارسی بی‌فایده است. چالش‌های واقعی:
نیم‌فاصله U+200C («می‌شود» ≠ «میشود»)، ی/ي و ک/ك (عربی↔فارسی)، ة/ه، أ/إ/آ/ا، اعراب‌گذاری،
کشیده U+0640، ارقام عربی/فارسی/لاتین، تنوین، و «هٔ» (U+06C0). همچنین غلط املایی رایج است.

## Decision
1. تابع خالص + SQL-هم‌ارز `normalize_fa(text)` با مراحل صریح و تست‌شده:
   - حذف اعراب (U+064B..U+0652)، حذف کشیده (U+0640)، حذف ZWJ/ZWNJ → فاصله.
   - یکسان‌سازی: ي→ی، ك→ک، ة→ه، أ/إ/آ→ا، ھ→ه، ؤ→و، ئ→ی، هٔ→ه، ة→ه.
   - ارقام عربی/فارسی → لاتین.
   - collapse whitespace + lowercase برای لاتین.
   - (اختیاری، در layer دوم) حذف «الـ» تعریف در ابتدای واژه‌های عربی.
2. هر entity یک ستون `search_text` (نرمال‌شده، شامل همه‌ی نام‌ها + خلاصه + aliasها) دارد
   که در service layer نگهداری می‌شود (نه trigger جادویی — قابل تست و قابل دیباگ).
3. ایندکس: `GIN (search_text gin_trgm_ops)` با `pg_trgm` → substring/fuzzy کار می‌کند؛
   به‌علاوه `GIN (to_tsvector('simple', search_text))` برای token match.
4. رتبه‌بندی: (تطابق نام preferred) > (تطابق alias) > (تطابق متن) ، سپس `importance/rank`،
   سپس نزدیکی به مرکز نقشه/زمان فعال (context-aware ranking).
5. جست‌وجوی ساخت‌یافته («بناهای صفوی اطراف اردبیل») **search نیست**؛ یک atlas query است:
   `GET /api/v1/atlas/query?kind=building&period=safavid&near=plc_ardabil&radius_km=20`.

## Consequences
- مثبت: بدون وابستگی خارجی (Meilisearch) تا وقتی واقعاً لازم شود؛ رفتار قابل پیش‌بینی.
- منفی: stemmer فارسی نداریم → «مسجدها» و «مسجد» با trgm پوشش داده می‌شوند ولی دقیق نیست.
- مسیر ارتقا: اگر دقت کافی نبود، Meilisearch/Typesense با sync از `search_text` (همان قرارداد).
