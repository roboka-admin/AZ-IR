# ADR-0016 — PostGIS: seed از loader fixtures، دسترسی مکانی با SQL صریح، تستِ drift

**Status:** Accepted · **Date:** 2026-09-12 · **Relates to:** ADR-0002, ADR-0004, ADR-0007, ADR-0014

## Context
ADR-0014 دو driver را وعده داد: `fixtures` برای dev/demo و `postgis` برای حقیقتِ production.
هنگام پیاده‌سازی سه پرسش واقعی پیش آمد:

1. **seeder از کجا داده می‌گیرد؟** اگر seed منطق نرمال‌سازی (تبدیل تقویم، precision، rank،
   هندسهٔ مشتق‌شده، نام‌ها) را دوباره پیاده کند، دو مسیر نوشتن داده خواهیم داشت و روزی
   divergence پیدا می‌کنند.
2. **دسترسی مکانی با ORM یا SQL؟** GeoAlchemy2 انواع `geometry` را به ORM می‌آورد، اما ما
   read-model داریم نه object graph؛ و هر query مکانی ما یک تابع مشخص PostGIS است
   (`ST_Intersects`, `ST_DWithin(::geography)`, `ST_SimplifyPreserveTopology`, `ST_PointOnSurface`).
3. **چطور بفهمیم schema و migrations و SQL با هم جورند؟** بدون یک Postgres در دسترس (sandbox،
   لپ‌تاپ بدون Docker) بیشتر باگ‌های این لایه فقط در production لو می‌روند.

## Decision

### ۱) loader fixtures نرمال‌ساز است؛ seeder فقط persist می‌کند
`azir seed` یک `FixturesRepository` می‌سازد و **همان رکوردهای دامنه** را می‌نویسد:
نام‌های fold شده، تقویم‌های تبدیل‌شده، `rank`/`layer`/`min_zoom`/`max_zoom` محاسبه‌شده در
`domain.semantic_zoom`، و هندسه‌های مشتق‌شده (`uncertain_locus`) با همان پرچم‌ها.

* `rank` و باند‌های zoom **ستون** هستند، نه محاسبه در SQL. SQL فقط با آن‌ها sort/filter می‌کند.
  این تنها راهی است که دو driver می‌توانند رفتار یکسان داشته باشند.
* جدول‌های claim (`assertion`/`evidence`) و لینک‌های ساختاری (`place_link`, `event_place`, …)
  از **raw documents** نوشته می‌شوند، چون حقیقت پژوهشی همان tripleهاست نه Relationshipهای
  مشتق‌شده؛ repository در زمان خواندن آن‌ها را به `Relationship` تبدیل می‌کند.

### ۲) SQL صریح + PostGIS؛ بدون GeoAlchemy2
`geoalchemy2` از وابستگی‌ها حذف شد (AGENTS.md قاعدهٔ ۱۴). به‌جایش:

* `repositories/postgis/schema.py` یک **Core mirror** است (فقط نوع/ستون، بدون ORM session).
* ستون مکانی `geom geometry(Geometry,4326)` در DDL اعلام می‌شود و از `geom_json` (کشِ GeoJSON)
  با `ST_SetSRID(ST_GeomFromGeoJSON(...), 4326)` پر می‌شود؛ بنابراین یک شکل خراب **در زمان
  seed** با صدای بلند شکست می‌خورد، نه بعداً به‌صورت نقشهٔ خالی.
* همهٔ queryها `sqlalchemy.text()` با پارامترهای نام‌دار هستند؛ هیچ رشته‌ای با دادهٔ کاربر
  ساخته نمی‌شود.
* `ST_DWithin(::geography)` شعاع را به **متر** می‌سنجد (fixtures در Python با تقریب
  equirectangular کار می‌کند و همان را در کد مستند کرده است).

### ۳) سه لایه تست
| لایه | کجا | چه چیزی |
|------|-----|---------|
| static drift | `tests/test_postgis_schema.py` (بدون DB) | هر جدول/ستونِ mirror در DDL باشد؛ ستون‌های SELECT دقیقاً همان‌هایی باشند که mapper می‌خواند؛ indexهای مکانی/زمانی/rank وجود داشته باشند؛ تابع folding همان code pointها را ببندد |
| contract | `tests/test_repository_contract.py` (با `AZIR_TEST_DB_URL`) | یک suite، دو driver — در CI اجباری |
| parity | `tests/test_postgis_contract.py` (marker `postgis`) | خروجی `azir_normalize_fa` برابر `textnorm.normalize_fa`؛ یکسان بودن id/rank/name/counts بین دو driver؛ شعاع/BBox/LOD/زمان واقعاً فیلتر کنند؛ seed دوبار = یکسان |

### ۴) ابزار عملیاتی
`python -m azir.cli` با argparse (بدون وابستگی جدید): `seed`, `migrate`, `doctor`, `serve`.
`doctor` قواعد Data Lint را اجرا می‌کند (claim بی‌شاهد، منتشر بدون نام/هندسه،
`extent_reconstructed` با `certainty=exact`) و در صورت مشکل exit code 1 می‌دهد — یعنی در CI
قابل استفاده است. stdout فقط JSON است و logها به stderr می‌روند.

## Consequences
* مثبت: یک مسیر نرمال‌سازی؛ parity قابل اثبات؛ بدون وابستگی ORM مکانی؛ باگ‌های schema پیش از
  اجرا دیده می‌شوند.
* منفی: SQL دستی باید با hand نگه داشته شود (mirror + تست drift هزینه‌اش را پوشش می‌دهد)؛
  `extra` در JSONB نگه داشته می‌شود تا payloadها با driver fixtures یکی بمانند، پس چند فیلد
  (coverage_note, map_state) هم ستون دارند هم در `extra` — ستون‌ها برای فیلتر/گزارش، `extra`
  برای read model.
* بعدی: وقتی نوشتن (editorial) آمد، همان `build_temporal`/`compute_rank` در service استفاده
  می‌شود و seeder به یک ingest pipeline تبدیل می‌گردد، بدون تغییر schema.
