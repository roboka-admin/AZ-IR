# ADR-0004 — یک `Place` با `kind` + facet؛ و Event سلسله‌مراتبی

**Status:** Accepted · **Date:** 2026-09-12

## Context
دست‌نوشته `Place` را با انواع building/castle/mosque/… تعریف می‌کند و در عین حال `Building` را
entity مستقل می‌داند. همچنین می‌پرسد آیا `War` باید entity جدا باشد یا نوعی `Event`.

## Decision
### ۱) Place تنها موجودیت مکانی است
```text
place(id, kind, parent_hint, status, importance, revision, ...)
kind: region|historical_region|province_modern|city|town|village|district|neighborhood
      |site|archaeological_site|building|mosque|madrasa|caravanserai|bazaar|bridge|castle
      |citadel|cemetery|mountain|river|lake|pass|route|battlefield
place_facet(place_id, facet_kind, attrs JSONB)     -- building/archaeology/route/...
```
افزودن نوع جدید = افزودن مقدار به taxonomy (نه migration ساختاری).

### ۲) سلسله‌مراتب مکان، زمان‌دار و چندپدری است
```text
place_link(parent_id, child_id, kind, valid_from, valid_to, source_id)
kind: contains|part_of|admin_in|historically_in|located_in
```
یک شهر هم‌زمان می‌تواند `admin_in` استان اردبیل (امروزی) و `historically_in` «آذربایجان تاریخی» باشد.
**هیچ‌وقت** `admin_in` به‌عنوان مرز تاریخی تفسیر نمی‌شود (AGENTS.md #9).

### ۳) هندسه زمان‌دار است، نه یک ستون
```text
place_geometry(place_id, geom, kind, lod_min_zoom, lod_max_zoom,
               valid_from, valid_to, certainty, source_id, digitized_by)
kind: point|footprint|extent_reconstructed|modern_admin|route_alignment|uncertain_locus
```
یک political entity می‌تواند چند `extent_reconstructed` با بازه‌های متفاوت داشته باشد.

### ۴) War یک Event است
```text
event(id, kind, temporal..., attestation, scale, certainty_of_occurrence)
event_link(child_id, parent_id, kind)      -- part_of | caused_by | led_to | contemporaneous_with
event_place(event_id, place_id, role, geometry?, certainty)   -- role: site_of|besieged|route_through|affected
event_participant(event_id, actor_type, actor_id, role, side)  -- role: commander|participant|casualty|negotiator
```
پس: Battle ⊂ Campaign ⊂ War با `part_of`. هیچ entity جدیدی لازم نیست.

## Consequences
- مثبت: گراف یکنواخت (همه‌چیز place_id/event_id) → assertion model و URL و layerها ساده می‌شوند.
- منفی: فیلدهای اختصاصی نوع‌ها در `place_facet.attrs` (JSONB) می‌روند → type-safety در لایه‌ی Pydantic
  تضمین می‌شود، نه در DB.
- الزام: هر `kind` باید در یک taxonomy ثبت‌شده با منبع باشد (دسته‌بندی خودش یک انتخاب علمی است).
