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
| CDN | `/tiles/archive/atlas-{locale}-{ver}-{revision}.pmtiles` (immutable چون نامش شامل revision است) | ۱ سال |
| CDN | `/tiles/index.json` (pointer؛ کوتاه، چون انتشار یعنی عوض‌شدنش) | ۶۰s |
| CDN | `/tiles/{z}/{x}/{y}.pbf` (رندر پویا؛ فقط وقتی آرشیو نداریم) | ۳۰۰s + `ETag`/304 |

`data_revision` (بالاترین revision منتشرشده) در کلید cache است → انتشار داده cache را بی‌اعتبار می‌کند.

**Time bucketing:** `t` خام باعث انفجار کلید cache می‌شود. bucket از zoom تایم‌لاین می‌آید:
`era → 100 سال`، `century → 25`، `year → 1`.

## ۵. DB

- GiST روی geom و `int4range(year_from, year_to)`؛ partial index `WHERE status='published'`.
- `EXPLAIN (ANALYZE, BUFFERS)` برای ۵ query اصلی در `docs/sql/` بایگانی می‌شود (baseline).
- `statement_timeout` = ۵s؛ `work_mem` متناسب با plan PaaS.
- connection pool: `pool_size=5, max_overflow=10` با pgbouncer transaction mode.
- read replica وقتی write/read جدا شد (بعد از Pilot لازم نیست).

## ۶. Vector Tiles: ساخته شده، اندازه‌گیری‌شده (ADR-0018)
مسیر PMTiles پیاده‌سازی شد؛ محرک‌های زیر تعیین می‌کنند **کی روشنش کنیم** (ساختن آرشیو یک تصمیم
عملیاتی است، نه یک پرچم پنهان):
- بیش از ~۵۰٬۰۰۰ feature منتشرشده، یا
- viewportهایی که به‌طور منظم `meta.truncated=true` می‌دهند، یا
- p95 `/atlas/features` > ۳۰۰ms با ایندکس‌های درست.

اندازه‌گیری روی پیکرهٔ اردبیل (fixtures، z0..z10، هر زبان یک آرشیو):

| معیار | مقدار |
|-------|-------|
| تعداد tile | ۱٬۰۱۰ |
| اندازهٔ آرشیو | ۸۴۶٬۷۲۳ بایت (fa) / ۸۴۹٬۰۵۳ (en) |
| feature رندرشده | ۵٬۶۴۷ |
| blob/entry پس از dedupe + run-length | ۵۶۸ / ۶۶۹ |
| زمان ساخت | ~۲٫۵ ثانیه |
| بزرگ‌ترین tile | ۷٬۲۵۷ بایت (سقف degrade: ۴٬۰۰۰ feature) |

یعنی **کل اطلس در یک فایل زیر یک مگابایت** که روی هر object storage/CDN می‌نشیند، بدون tile server.
هزینهٔ واقعی مقیاس، build است نه سرو: با هر انتشار یک `make tiles` (یا job روی رویداد publish)،
وگرنه tileها عقب می‌مانند. زومِ بیشتر از `AZIR_TILES_MAX_ZOOM` در آرشیو نیست → یا build بزرگ‌تر، یا
رندر پویا که همچنان سر جایش است.
