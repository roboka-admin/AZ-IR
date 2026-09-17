# ADR-0002 — Modular monolith روی PostgreSQL + PostGIS

**Status:** Accepted · **Date:** 2026-09-12

## Context
دامنه شامل داده‌ی مکانی (Point/Polygon/LineString)، زمانی (بازه‌های چندصدساله)، رابطه‌ای (knowledge graph)
و متنی (مقاله/منبع) است. گزینه‌ها: Postgres+PostGIS، Mongo+GeoJSON، Neo4j، Elasticsearch+GIS.

## Decision
1. **یک پایگاه‌داده: PostgreSQL 15+ با PostGIS 3.4+**. همه‌ی state ماندگار آنجاست.
2. Backend یک **modular monolith** است (FastAPI) با مرزهای داخلی روشن:
   `api/routers → services (application) → domain (pure) → repositories (data access)`.
3. ماژول‌های دامنه‌ای: `atlas` (map/timeline), `entity`, `editorial` (article/source/workflow), `identity` (user/role), `search`.
4. هیچ ماژولی مستقیماً به جدول ماژول دیگر SQL نمی‌زند؛ فقط از طریق repository/contract خودش.

## Alternatives considered
- **Neo4j**: گراف را بهتر بیان می‌کند ولی spatial/temporal ضعیف‌تر، عملیات سنگین‌تر، و مدل assertion ما triple-based است که در Postgres هم کافی است. مسیر فرار: export assertion store به RDF.
- **میکروسرویس از روز اول**: برای تیم کوچک و داده‌ی پژوهشی، هزینه‌ی توزیع‌شدگی بدون منفعت.
- **SQLite/SpatiaLite برای dev**: وسوسه‌کننده ولی واگرایی رفتار با prod (PostGIS-only functions). به‌جایش ADR-0014.

## Consequences
- مثبت: یک زبان query برای مکان+زمان+گراف+متن، transactional، ایندکس‌های GiST/GIN/BRIN، عملیات ساده روی PaaS (Neon/Supabase هر دو PostGIS دارند).
- منفی: سقف مقیاس افقی؛ وقتی لازم شد، ماژول‌ها را بر اساس همان مرزها بیرون می‌کشیم (search، tiles).
- الزام: هر query مکانی باید از PostGIS استفاده کند (AGENTS.md #11)؛ فیلتر bbox با lat/lng عددی ممنوع است (مگر در dev driver مستند).
