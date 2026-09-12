# کارایی و مقیاس

## ۱. فرض حجم داده

| مرحله | place | person/event | assertion | article | هندسه |
|-------|-------|--------------|-----------|---------|-------|
| Pilot (اردبیل) | ~۵۰ | ~۵۰ | ~۲۰۰ | ~۱۰ | ~۱۰۰ |
| کل آذربایجان ایران | ~۵٬۰۰۰ | ~۲۰٬۰۰۰ | ~۱۰۰٬۰۰۰ | ~۵۰۰ | ~۱۵٬۰۰۰ |
| بلندمدت | ~۵۰٬۰۰۰ | ~۲۰۰٬۰۰۰ | ~۱٬۰۰۰٬۰۰۰ | ~۵٬۰۰۰ | ~۱۵۰٬۰۰۰ |

طراحی برای ستون «بلندمدت» انجام می‌شود؛ بهینه‌سازی فقط وقتی که اندازه‌گیری نشان دهد لازم است.

## ۲. SLO

| معیار | هدف |
|-------|-----|
| `/atlas/features` p95 (viewport معمولی، ≤۵۰۰ feature) | **≤ ۳۰۰ ms** (server) |
| `/atlas/features` p99 | ≤ ۸۰۰ ms |
| entity detail p95 | ≤ ۲۰۰ ms |
| search p95 | ≤ ۴۰۰ ms |
| First map paint (cache گرم) | ≤ ۲ s روی 4G |
| Timeline scrub | ۶۰ fps (فیلتر local، بدون network per-frame) |
| payload یک پاسخ نقشه | ≤ **۵۱۲ KB** gzip (با degrade و `meta.truncated`) |

## ۳. بودجه‌ی payload و degrade (ترتیب حذف)

وقتی پاسخ از بودجه بیشتر شد، service به این ترتیب کم می‌کند و در `meta` گزارش می‌دهد:
1. `fields=full → default → min` (حذف summary/relationships)
2. کاهش دقت هندسه (LOD بالاتر)
3. کاهش `limit` بر اساس `rank` (کم‌اهمیت‌ترین‌ها حذف می‌شوند) + `next_cursor`
4. در آخرین حالت: فقط لایه‌های فعال کاربر + هشدار UI «نتایج کامل نیست»

**هرگز** بی‌صدا truncate نمی‌کنیم.

## ۴. Cache

| لایه | چه چیزی | TTL |
|------|---------|-----|
| CDN | `/atlas/features` با کلید (bbox rounded, zoom bucket, **time bucket**, layers, locale, data_revision) | ۶۰s + SWR ۶۰۰s |
| CDN | `/meta`, `/atlas/layers` | ۳۰۰s |
| HTTP | ETag روی همه‌ی GETها → 304 | – |
| app | LRU کوچک برای name resolution و taxonomy | in-process |
| client | featureها در memory با `t_from/t_to` → فیلتر local هنگام scrub | تا تغییر viewport/bucket |

`data_revision` (بالاترین revision منتشرشده) در کلید cache است → انتشار داده cache را بی‌اعتبار می‌کند.

**Time bucketing:** `t` خام باعث انفجار کلید cache می‌شود. bucket از zoom تایم‌لاین می‌آید:
`era → 100 سال`، `century → 25`، `year → 1`.

## ۵. DB

- GiST روی geom و `int4range(year_from, year_to)`؛ partial index `WHERE status='published'`.
- `EXPLAIN (ANALYZE, BUFFERS)` برای ۵ query اصلی در `docs/sql/` بایگانی می‌شود (baseline).
- `statement_timeout` = ۵s؛ `work_mem` متناسب با plan PaaS.
- connection pool: `pool_size=5, max_overflow=10` با pgbouncer transaction mode.
- read replica وقتی write/read جدا شد (بعد از Pilot لازم نیست).

## ۶. چه وقت Vector Tiles لازم می‌شود؟
محرک‌های عینی (نه حسی):
- بیش از ~۵۰٬۰۰۰ feature منتشرشده، یا
- viewportهایی که به‌طور منظم `meta.truncated=true` می‌دهند، یا
- p95 `/atlas/features` > ۳۰۰ms با ایندکس‌های درست.
→ آن‌وقت ADR-0011 اجرا می‌شود (PMTiles + فیلتر زمان سمت کلاینت).
