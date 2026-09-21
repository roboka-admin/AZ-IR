# ADR-0011 — تحویل داده‌ی نقشه: GeoJSON امروز، PMTiles فردا

**Status:** Accepted · **Date:** 2026-09-12 · **Context:** استقرار روی PaaS/CDN

## Decision
### فاز جاری (≤ ~50k feature)
`GET /api/v1/atlas/features` → GeoJSON با field budget (ADR-0009)، cache در CDN،
ساده‌سازی هندسه سمت سرور متناسب با zoom (LOD).

### فاز بعد (≥ ~50k feature یا viewportهای شلوغ)
**PMTiles** (یک فایل، HTTP range requests، بدون tile server اختصاصی → روی هر object storage/CDN).
- تولید: job زمان‌بندی‌شده (`make tiles`) که snapshot منتشرشده را به tiles زمان‌برش‌خورده تبدیل می‌کند.
- چون داده‌ی ما زمان‌دار است، tileها **بازه‌ی زمانی هر feature** را به‌عنوان attribute حمل می‌کنند
  (`t_from`, `t_to`) و فیلتر زمان **سمت کلاینت** انجام می‌شود؛ فقط وقتی پنجره‌ی زمانی از حد tile بیرون است
  refetch می‌شود. این یعنی scrub کردن timeline بدون network per-frame.
- نسخه‌بندی tiles: `atlas-{schema_version}-{data_revision}.pmtiles` + pointer file `atlas-latest.pmtiles`
  → اتمیک بودن انتشار و rollback.

### Basemap
- style سفارشی (JSON در repo) با:
  - terrain/hillshade کم‌رنگ (برای حس 2.5D بدون سنگینی 3D)،
  - **دو حالت**: `modern` (نام‌های امروزی) و `historical` (نام‌های امروزی کم‌رنگ/حذف، مرزهای مدرن نقطه‌چین)،
  - فونت self-hosted (Vazirmatn برای fa؛ Noto Sans برای لاتین) → بدون وابستگی به Google Fonts (مهم برای کاربران داخل ایران).
- **Fallback آفلاین**: اگر style/tile خارجی در دسترس نبود، frontend به style داخلی حداقلی
  (پس‌زمینه + گرید + لایه‌های خودمان) سوییچ می‌کند و بنر «basemap محدود» نشان می‌دهد. نقشه هرگز سفید نمی‌ماند.

## Consequences
- مثبت: بدون سرور tile؛ هزینه نزدیک صفر؛ انتشار اتمیک؛ scrub نرم.
- منفی: tileها از قبل تولید می‌شوند → داده‌ی `draft` روی tiles نیست (درست هم همین است: فقط published عمومی می‌شود).
  برای preview ویرایش‌ها از مسیر GeoJSON زنده استفاده می‌کنیم.
