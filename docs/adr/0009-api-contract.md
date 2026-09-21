# ADR-0009 — قرارداد API نسخه‌ی ۱

**Status:** Accepted · **Date:** 2026-09-12

## Decision
### مسیر و نسخه
```
/api/v1/…            نسخه‌دار در path (نه header)؛ تغییر breaking = /api/v2
```
### پاسخ‌ها
- JSON؛ GeoJSON (RFC 7946) برای هر خروجی مکانی.
- پاکت فهرست‌ها: `{ data: [...], page: { next_cursor, prev_cursor, limit, total_estimate } , meta: {...} }`.
- خطاها: **RFC 9457 Problem Details** → `{ type, title, status, detail, instance, errors[] , request_id }`.
- `request_id` (UUID) روی هر پاسخ در header `X-Request-Id` و در لاگ‌ها.

### Pagination
cursor-based (نه offset) روی کلید `(sort_key, id)`؛ `limit` پیش‌فرض ۵۰، حداکثر ۲۰۰.

### Sparse fieldsets و field budget (برای نقشه)
```
GET /api/v1/atlas/features?bbox=…&zoom=7&t=1450&mode=at&layers=places,buildings
   &fields=min          -- min|default|full
```
`fields=min` فقط آنچه برای رندر لازم است برمی‌گرداند (id, kind, rank, label, geometry ساده‌شده).
هدف: payload ≤ 512KB؛ اگر بیشتر شد، service با ترتیب `rank` قطع می‌کند و `meta.truncated=true`
+ `meta.next_cursor` می‌دهد. **هرگز** بدون اطلاع، بی‌صدا truncate نمی‌کنیم.

### Caching
- `ETag` (هش محتوای پاسخ) + `If-None-Match` → 304.
- `Cache-Control: public, max-age=60, stale-while-revalidate=600` برای atlas؛
  برای entity detail `max-age=300` چون داده‌ی تاریخی سریع عوض نمی‌شود.
- کلید cache شامل **time bucket** است نه `t` خام (مثلاً bucket اندازه‌اش از zoom تایم‌لاین: 1/10/50/100 سال).

### Rate limiting
per-IP token bucket (پیش‌فرض 60 req/min برای anonymous، 600 برای authenticated).
در PaaS با middleware داخلی + (اختیاری) لبه‌ی CDN.

### Idempotency
همه‌ی writeهای editorial با `Idempotency-Key` header (۲۴ ساعت نگه‌داشته می‌شود).

### OpenAPI
- schemaها از Pydantic مشتق می‌شوند؛ `/docs` و `/api/v1/openapi.json`.
- **contract test** در CI: snapshot از openapi.json با diff بررسی می‌شود تا تغییر ناخواسته‌ی breaking دیده شود.

## Consequences
- مثبت: frontend و third-party می‌توانند مستقل کار کنند؛ cache در CDN ممکن است.
- منفی: پیاده‌سازی ETag/cursor هزینه دارد → در Phase 1 فقط ساختار و headerها؛ منطق کامل در Phase 5.
