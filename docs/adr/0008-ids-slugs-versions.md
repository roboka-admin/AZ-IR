# ADR-0008 — شناسه، slug، و نسخه‌ی قابل استناد

**Status:** Accepted · **Date:** 2026-09-12

## Context
لینک‌های یک اطلس پژوهشی باید ده‌ها سال زنده بمانند و بتوان در مقاله‌ی علمی به یک «وضعیت مشخص» استناد کرد.

## Decision
1. **Primary key = UUIDv7** با prefix خوانا در سطح API/URL:
   `plc_01J9X...` (place), `prs_...` (person), `evt_...` (event), `pol_...` (political entity),
   `prd_...` (period), `art_...` (article), `src_...` (source), `asn_...` (assertion),
   `evd_...` (evidence), `usr_...` (user). جدول کامل prefixها در `azir/core/ids.py`.
   UUIDv7 چون زمان‌مرتب است، B-tree index را fragment نمی‌کند.
2. **slug** = رشته‌ی خوانا و یکتا در هر نوع (`sheikh-safi-complex`)، ساخته‌شده از نام preferred لاتین یا
   ترانویسی آن؛ **قابل تغییر** است و تغییر آن یک ردیف در `slug_redirect(old, new, changed_at)` می‌سازد.
3. **revision** = عدد صعودی روی هر entity؛ هر write موفق `revision += 1`.
   URL نسخه‌دار: `/place/plc_x?v=12` → snapshot قابل استناد (از audit_log بازسازی می‌شود).
4. **حذف فیزیکی ممنوع**: `status = archived` + tombstone در API (410 Gone برای عمومی، قابل دیدن برای editor).
5. API همیشه `id` را برمی‌گرداند؛ `slug` فقط برای URL انسانی است.

## Consequences
- مثبت: لینک پایدار، ارجاع‌پذیری علمی، دیباگ راحت (prefix نشان می‌دهد موجودیت چیست).
- منفی: رشته‌های id طولانی‌تر از int هستند → در payloadهای بزرگ map، `id` کوتاه‌شده ارسال می‌شود؟
  نه؛ در عوض `fields=sparse` برای payload نقشه (ADR-0009) و id کامل فقط در detail.
