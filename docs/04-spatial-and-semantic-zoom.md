# مدل مکانی و Semantic Zoom

> مرجع: ADR-0004 (geometry زمان‌دار), ADR-0011 (delivery), ADR-0013 (مرزها)

## ۱. ذخیره‌سازی

- CRS ذخیره: **EPSG:4326** (lon/lat). CRS رندر: Web Mercator (توسط MapLibre).
- انواع هندسه: `Point`, `LineString`, `Polygon`, `MultiPolygon` — هر `place_geometry` می‌تواند هرکدام باشد.
- یک place می‌تواند **چند** هندسه با بازه‌ی زمانی متفاوت داشته باشد (مثلاً سه مرحله‌ی گسترش یک شهر).
- `certainty ∈ exact | approximate | uncertain | reconstructed`.

## ۲. LOD (سطح جزئیات) متناسب با zoom

| zoom | رفتار |
|------|-------|
| ≤ 5 | `ST_SimplifyPreserveTopology(geom, 0.05)`؛ فقط `extent_reconstructed` و شهرهای بزرگ |
| 6–8 | tolerance 0.01 |
| 9–11 | tolerance 0.001 |
| ≥ 12 | هندسه‌ی اصلی |

toleranceها در `domain/semantic_zoom.py` به‌صورت جدول‌شده و قابل تنظیم‌اند و در تست golden بررسی می‌شوند.

## ۳. سطوح Semantic Zoom (قرارداد داده، نه ترفند UI)

| level | zoom | دامنه | چه چیزی برگردانده می‌شود |
|-------|------|-------|--------------------------|
| `L0_region` | 0–6 | کل آذربایجان | فقط `region|historical_region|city` با `rank ≥ 70`، بدون جزئیات |
| `L1_area` | 7–9 | منطقه | `city|town|archaeological_site|major_event|political_extent` با `rank ≥ 45` |
| `L2_city` | 10–12 | شهر | همه‌ی placeهای شهری، `building` مهم، رویدادها، اشخاص مرتبط |
| `L3_fabric` | 13–15 | بافت تاریخی | محله‌ها، همه‌ی بناها، کاروانسراها، پل‌ها، قبرستان‌ها |
| `L4_monument` | 16+ | بنا | footprint دقیق، اجزای بنا (از facet)، تغییرات تاریخی |

هر feature در پاسخ API این‌ها را دارد:
```json
{ "rank": 82, "min_zoom": 6, "max_zoom": 16, "level": "L1_area", "label": "اردبیل", "label_secondary": "Ardabil" }
```

## ۴. فرمول `rank` (شفاف، مشتق از داده، قابل audit)

```
rank = clip(0, 100,
      35 * editorial_importance          # 0..1 دستیِ پژوهشی (وزن اصلی)
    + 20 * kind_weight[kind]             # region/city بالاتر از village
    + 15 * norm(source_count)            # log-scaled تعداد منبع
    + 12 * norm(assertion_count)         # چقدر درباره‌اش ادعای مستند داریم
    + 10 * norm(article_count)           # پوشش محتوایی
    +  8 * period_coverage               # چند دوره‌ی زمانی را پوشش می‌دهد
)
```

`norm(x) = min(1, log(1+x)/log(1+K))` با `K` متناسب با نوع (مثلاً K=20 برای source).
خروجی در `place.rank_cache` materialize می‌شود (در `ProjectionService`) و **هرگز دستی override نمی‌شود**
مگر با `importance` که خودش audit می‌شود.

> **Historical Density** (ایده‌ی بخش ۲۱ دست‌نوشته) از همین‌جا مشتق می‌شود:
> `density(cell) = Σ rank of features in cell / area` و به‌صورت یک لایه‌ی heat/grid در zoom پایین رندر می‌شود.
> این لایه در Phase 11 فعال می‌شود؛ زیرساختش (rank + grid aggregation) الان ساخته می‌شود.

## ۵. Queryهای مکانی مورد نیاز

| نیاز | PostGIS |
|------|---------|
| viewport | `geom && ST_MakeEnvelope(...)` سپس `ST_Intersects` |
| داخل منطقه | `ST_Within(geom, region_geom)` |
| شعاع N کیلومتر | `ST_DWithin(geom::geography, pt::geography, N*1000)` |
| ساده‌سازی | `ST_SimplifyPreserveTopology` |
| مرکز برای label | `ST_PointOnSurface` |
| مساحت/طول | `ST_Area(geom::geography)`, `ST_Length(...)` |
| aggregation چگالی | `ST_SnapToGrid` + `GROUP BY` |
| تبدیل به GeoJSON | `ST_AsGeoJSON(geom, 6)` |

## ۶. مرزها و عدم‌قطعیت (بصری)

| certainty | رندر |
|-----------|------|
| `exact` | خط توپر ۲px + هاله‌ی روشن |
| `approximate` | خط نقطه‌چین ۲px |
| `uncertain` / `reconstructed` | fill محو + هاشور + برچسب «بازسازی پژوهشی» |
| `modern_admin` | خط خاکستری نازک نقطه‌چین، لایه‌ی پیش‌فرض **خاموش** |

و disclaimer دائمی نقشه (ADR-0013): «مرزهای تاریخی بازسازی پژوهشی‌اند و با مرزهای سیاسی امروزی مطابقت ندارند.»
