# معماری سیستم

> مرجع: ADR-0002 (modular monolith), ADR-0014 (drivers), ADR-0009 (API contract)

## ۱. نمای کلی

```
                       ┌──────────────────────────────────────────────┐
                       │                  Browser                     │
                       │  Next.js App (RSC + client) ── MapLibre GL   │
                       │  RTL/fa-first · URL-synced atlas state       │
                       └───────────────┬──────────────────────────────┘
                                       │  relative URLs only  (/api/v1/…)
                        ┌──────────────▼───────────────┐
                        │  Next.js rewrites / edge CDN │   (proxy → backend)
                        └──────────────┬───────────────┘
                                       │
        ┌──────────────────────────────▼───────────────────────────────┐
        │                    FastAPI application (api/v1)               │
        │  middleware: request-id · timing · problem-details · CORS     │
        │  routers: health · meta · atlas · entities · articles · search│
        └──────────────────────────────┬───────────────────────────────┘
                                       │ calls (no logic in routers)
        ┌──────────────────────────────▼───────────────────────────────┐
        │                      services (application)                   │
        │  AtlasService · TimelineService · EntityService ·             │
        │  ArticleService · SearchService · ProjectionService           │
        └───────────┬──────────────────────────────────┬───────────────┘
                    │ uses                             │ uses
   ┌────────────────▼───────────────┐   ┌──────────────▼────────────────┐
   │      domain (pure, no I/O)     │   │   repositories (ports)        │
   │ temporal · calendar · zoom ·   │   │  MapRepository · EntityRepo · │
   │ ranking · text normalization · │   │  TimelineRepo · ArticleRepo · │
   │ field budget · interval algebra│   │  SearchRepo                   │
   └────────────────────────────────┘   └───────┬──────────────┬────────┘
                                                │              │
                                    ┌───────────▼──────┐  ┌────▼─────────────┐
                                    │  postgis adapter │  │ fixtures adapter │
                                    │  SQLAlchemy+ST_* │  │  YAML + shapely  │
                                    └───────────┬──────┘  └────┬─────────────┘
                                                │              │
                                       ┌────────▼──────────────▼───────┐
                                       │  PostgreSQL 15+ / PostGIS 3.4 │
                                       └───────────────────────────────┘
```

قانون جهت وابستگی: `api → services → domain` و `services → repositories`. هیچ پیکان برگشتی وجود ندارد.
`domain` هیچ import از FastAPI/SQLAlchemy ندارد (با lint معماری در CI بررسی می‌شود).

## ۲. ماژول‌های دامنه

| ماژول | مسئولیت | جدول‌ها |
|-------|---------|---------|
| `atlas` | viewport+time → features؛ تایم‌لاین چگالی؛ semantic zoom | `place`, `place_geometry`, `event`, `political_entity` |
| `entity` | جزئیات موجودیت، روابط گراف، nameها | همه‌ی entityها + `assertion`, `place_link` |
| `provenance` | منبع، evidence، claim، استناد | `source`, `evidence`, `assertion` |
| `editorial` | مقاله، گردش‌کار، نسخه، audit | `article`, `article_entity`, `audit_log`, `revision` |
| `taxonomy` | واژگان کنترل‌شده، دوره‌ها | `taxonomy`, `period_scheme`, `period` |
| `identity` | کاربر، نقش، احراز هویت | `app_user`, `role`, `session` |
| `search` | نرمال‌سازی، ایندکس، رتبه‌بندی | `search_index` (derived) |

## ۳. جریان یک درخواست نقشه (نمونه‌ی واقعی)

```
GET /api/v1/atlas/features?bbox=45.9,37.6,48.9,38.9&zoom=8&t=1450&mode=at&layers=places,buildings&fields=min

router   → validate & parse (pydantic): bbox → BBox, t+mode+cal → TimeWindow, zoom → ZoomLevel
service  → AtlasService.features(query):
             1. semantic zoom level از zoom مشتق می‌شود (doc 04)
             2. min_rank آستانه از (zoom_level, layer, density) حساب می‌شود
             3. repository.features(bbox, window, layers, min_rank, lod, limit)
             4. projection: display name برای locale، label کوتاه، رنگ/آیکون از design tokens
             5. field budget: اگر payload > 512KB → truncate by rank + meta.truncated
             6. cache key/ETag
repo     → postgis:  ST_MakeEnvelope + && + ST_Intersects + int4range && + GiST + ST_SimplifyPreserveTopology
           fixtures: shapely intersects + همان domain logic
response → GeoJSON FeatureCollection + meta{driver, zoom_level, time, truncated, coverage, request_id}
```

نکته‌ی کلیدی: **MapLibre هیچ‌کدام از مراحل ۱ تا ۵ را نمی‌داند.** فقط رندر می‌کند.

## ۴. جریان scrub کردن Timeline (بدون network per-frame)

```
کاربر timeline را می‌کشد
  → کلاینت: فیلتر زمانی local روی featureهایی که قبلاً با پنجره‌ی بزرگ‌تر گرفته شده
            (هر feature از API دارای t_from/t_to است)
  → commit (debounce 250ms یا تغییر bucket زمانی):
            refetch با پنجره‌ی جدید
  → bucketهای زمانی: 1 / 5 / 25 / 100 / 500 سال (بر اساس zoom تایم‌لاین) → کلید cache
```

## ۵. استقرار (PaaS)

```
frontend  → Vercel/Netlify (Next.js standalone) یا Node روی PaaS ایرانی (لیارا/آروان)
backend   → container (FastAPI + uvicorn) روی PaaS یا Fly/Render
database  → Neon یا Supabase (هر دو PostGIS دارند) + pgbouncer transaction pooling
tiles     → PMTiles روی object storage/CDN (R2/S3/لیارا) — بدون tile server اختصاصی
assets    → همان CDN؛ فونت‌ها self-hosted
```

الزامات PaaS که از روز اول رعایت می‌شوند:
1. **12-factor**: همه‌ی config از env (`AZIR_*`)؛ هیچ فایل پیکربندی در runtime خوانده نمی‌شود.
2. **stateless backend**: هیچ session در حافظه؛ cookie-based session در DB.
3. **connection pooling**: `pool_pre_ping`, `pool_size` متناسب با limitهای pgbouncer؛ statement_timeout.
4. **graceful shutdown** و `/healthz` + `/readyz` (readyz به DB وصل می‌شود).
5. **محدودیت serverless**: اگر backend روی Vercel/lambda رفت، queryها باید کوتاه باشند
   (`AZIR_DB_STATEMENT_TIMEOUT_MS` پیش‌فرض ۵۰۰۰) و هیچ کار پس‌زمینه‌ای در process انجام نشود
   → jobهای تولید tile جدا (GitHub Actions) اجرا می‌شوند.

## ۶. Observability

- **logs**: JSON ساخت‌یافته (`request_id`, `route`, `status`, `duration_ms`, `driver`, `locale`, `query_key`).
- **metrics** (endpoint `/metrics` بعداً): شمارنده‌ی درخواست‌ها، histogram مدت‌پاسخ، تعداد feature بازگشتی، نرخ truncate.
- **tracing**: header `X-Request-Id` از لبه تا DB (به `audit_log` هم می‌رسد).
- **error budget**: هر 5xx یک رکورد با stack در لاگ + `request_id` در پاسخ کاربر (برای پشتیبانی).

## ۷. امنیت

- API عمومی: **read-only**. هیچ write بدون احراز هویت.
- writeها: cookie session (HttpOnly, SameSite=Lax, Secure) + نقش (ADR-0010).
- CORS: فقط originهای شناخته‌شده از env؛ در dev `localhost` و preview host.
- rate limit روی anonymous؛ body size limit؛ پارامترهای bbox/limit محدود (جلوگیری از full-scan).
- SQL همیشه parameterized (SQLAlchemy bind params) — هیچ string interpolation.
- هیچ داده‌ی منتشرنشده (`draft`) در پاسخ عمومی ظاهر نمی‌شود.
