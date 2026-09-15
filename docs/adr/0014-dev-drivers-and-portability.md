# ADR-0014 — پورت‌پذیری: PostGIS در prod، fixture driver در محیط‌های بدون Docker

**Status:** Accepted · **Date:** 2026-09-12

## Context
مسیر اصلی PostgreSQL + PostGIS است (ADR-0002). اما محیط‌هایی وجود دارند که Postgres/PostGIS ندارند
(یک sandbox محدود، یک لپ‌تاپ بدون Docker، CI سریع). اگر توسعه‌دهنده نتواند پروژه را بالا بیاورد،
مشارکت‌کننده از دست می‌دهیم.

## Decision — Ports & Adapters
`MapRepository` یک **port** (protocol) است با دو adapter:

| driver | چه‌وقت | چه‌طور |
|--------|--------|--------|
| `postgis` | prod, staging, CI integration, docker-compose | SQL واقعی با `ST_*`، GiST، `int4range` |
| `fixtures` | dev سریع، demo، تست‌های واحد API | همان fixtureهای YAML که seeder هم می‌خواند؛ فیلتر مکانی با `shapely` در Python |

قواعد:
1. انتخاب با `AZIR_DB_DRIVER=postgis|fixtures`؛ اگر `fixtures` فعال باشد، **هر پاسخ API**
   `meta.driver="fixtures"` و header `X-AZIR-Driver: fixtures` می‌گیرد. هیچ‌وقت بی‌صدا fallback نمی‌کنیم.
2. منطق دامنه (temporal overlap، ranking، semantic zoom، field budget) در **service/domain** است و
   بین دو adapter مشترک → واگرایی رفتار به حداقل می‌رسد.
3. تست‌های PostGIS در CI با service container `postgis/postgis:16-3.4` اجرا می‌شوند و **اجباری**اند.
4. `fixtures` هرگز در production مجاز نیست (config guard: اگر `ENV=production` و driver=fixtures → startup fail).

## Consequences
- مثبت: onboarding با `make dev` بدون Docker؛ demo زنده در محیط‌های محدود؛ تست سریع.
- منفی: دو پیاده‌سازی data-access → با تست‌های contract مشترک (یک suite تست، دو بار با دو driver) مهار می‌شود.
