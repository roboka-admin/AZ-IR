# قرارداد API نسخه‌ی ۱

> مرجع: ADR-0009. OpenAPI زنده: `/docs` و `/api/v1/openapi.json`.
> این سند قرارداد است؛ اگر کد با آن تفاوت داشت، **کد غلط است**.

## ۰. قواعد عمومی

- Base: `/api/v1`
- Content-Type: `application/json; charset=utf-8` (خروجی مکانی: GeoJSON RFC 7946)
- هر پاسخ شامل header `X-Request-Id` و `X-AZIR-Driver` (`postgis|fixtures`) است.
- خطا: RFC 9457 → `application/problem+json`
```json
{ "type": "https://errors.azir.dev/validation", "title": "Invalid parameter",
  "status": 422, "detail": "bbox must have exactly 4 numbers", "instance": "/api/v1/atlas/features",
  "request_id": "…", "errors": [ {"field":"bbox","message":"…"} ] }
```
- Listها: `{"data":[…], "page":{"next_cursor":…,"limit":50,"total_estimate":…}, "meta":{…}}`
- زبان پاسخ با `?locale=fa|en` یا header `Accept-Language` (پیش‌فرض `fa`).
- همه‌ی endpointهای عمومی **read-only** هستند؛ writeها زیر `/api/v1/admin/*` با session + نقش.

## ۱. `GET /healthz` , `GET /readyz`
`healthz` همیشه 200 (liveness). `readyz` اتصال DB و driver را چک می‌کند و در صورت خرابی 503.

## ۲. `GET /api/v1/meta`
یک پاسخ برای bootstrapping کل کلاینت (بدون hard-code در frontend — قانون ۱۷):
```json
{ "api_version":"1", "driver":"fixtures", "env":"development",
  "locales":[{"code":"fa","dir":"rtl","default":true},{"code":"en","dir":"ltr"}],
  "study_area":{"name":"…","bbox":[44.0,35.5,49.5,39.8],"center":[47.0,38.0],"default_zoom":6.5},
  "timeline":{"floor":-800,"ceil":2026,"default_year":1500,"buckets":[1,5,25,100,500],"calendar_default":"gregorian_proleptic"},
  "layers":[…], "zoom_levels":[…], "periods":[…],
  "coverage":{"places":20,"people":10,"events":10,"articles":6,"sources":18,"disputed_assertions":2},
  "disclaimer":{"borders":"…"}, "generated_at":"…" }
```

## ۳. `GET /api/v1/atlas/layers`
```json
{"data":[{"id":"places","label_fa":"مکان‌ها","label_en":"Places","default_on":true,
          "min_zoom":5,"max_zoom":16,"geometry_types":["point","polygon"],
          "entity_kinds":["region","historical_region","city","town","village"],
          "style_token":"layer.places","count":14}, …]}
```
لایه‌ها: `places, buildings, archaeology, events, battles, people, political_entities, routes, modern_borders, articles`.

## ۴. `GET /api/v1/atlas/features` (اصلی‌ترین endpoint)

| param | نوع | پیش‌فرض | توضیح |
|-------|-----|---------|-------|
| `bbox` | `minLon,minLat,maxLon,maxLat` | study_area | محدود به حداکثر ۲۵° در ۲۵° |
| `zoom` | float 0..22 | 7 | سطح semantic zoom و LOD را تعیین می‌کند |
| `t` | int | timeline.default | «لحظه» (سال میلادی نرمال‌شده)؛ با `cal=AH|SH|JUL` قابل ورود با تقویم دیگر |
| `from`,`to` | int | – | پنجره‌ی زمانی؛ اگر باشد `t` نادیده گرفته می‌شود |
| `mode` | `at|during|overlaps` | `at` | ADR-0005 |
| `layers` | csv | همه‌ی لایه‌های default_on | |
| `kinds` | csv | – | فیلتر دقیق‌تر روی `place.kind` / `event.kind` |
| `fields` | `min|default|full` | `default` | field budget |
| `limit` | int ≤ 2000 | 800 | |
| `cursor` | str | – | ادامه‌ی pagination بر اساس `(rank DESC, id)` |
| `locale` | `fa|en` | `fa` | زبان label |

پاسخ: GeoJSON با properties:
```json
{"id":"plc_…","kind":"city","layer":"places","level":"L1_area","rank":82,
 "min_zoom":6,"max_zoom":14,"label":"اردبیل","label_secondary":"Ardabil","dir":"rtl",
 "t_from":1200,"t_to":1600,"t_display":"سدهٔ ۷ تا ۱۱ ق","t_precision":"century",
 "confidence":"high","status":"published","article_count":3,"source_count":5,
 "has_disagreements":false,"slug":"ardabil","href":"/api/v1/entities/place/plc_…"}
```
`meta`:
```json
{"driver":"fixtures","zoom_level":"L1_area","min_rank":45,"lod_tolerance":0.01,
 "time":{"mode":"at","from":1450,"to":1450,"calendar":"gregorian_proleptic"},
 "returned":37,"truncated":false,"payload_bytes":41233,"coverage_gaps":[],"request_id":"…"}
```

## ۵. `GET /api/v1/atlas/timeline`
برای رسم هیستوگرام/چگالی تایم‌لاین در محدوده‌ی دید:
```
?bbox=…&zoom=…&from=1000&to=1900&bucket=50&layers=…
```
```json
{"data":[{"from":1000,"to":1050,"counts":{"events":4,"places":11,"people":2,"buildings":1},
          "total":18,"top_kinds":["city","mosque"],"notable":[{"id":"evt_…","label":"…","year":1030}]}],
 "meta":{"bucket":50,"mode":"overlaps"}}
```

## ۶. `GET /api/v1/atlas/context`  («What was here?»)
```
?place_id=plc_…   یا   ?lat=38.25&lon=48.29&radius_km=5
```
همه‌ی موجودیت‌های مرتبط با آن نقطه/مکان، مرتب‌شده در زمان:
```json
{"place":{…},"range":{"from":800,"to":2026},
 "data":[{"t_from":1300,"t_to":1350,"entity_type":"person","id":"prs_…","label":"شیخ صفی‌الدین",
          "relation":"lived_in","predicate_label":"زیست در","source_count":4}],
 "periods":[…],"disagreements":[…]}
```

## ۷. `GET /api/v1/entities/{entity_type}/{id_or_slug}`
`entity_type ∈ place|person|event|political_entity|article|source|period`
```json
{"id":"plc_…","entity_type":"place","kind":"building","status":"published","revision":3,
 "names":{"display":"بقعهٔ شیخ صفی‌الدین اردبیلی","display_secondary":"Sheikh Safi al-Din Shrine",
          "alternatives":[{"form":"…","lang":"fa","kind":"historical"}]},
 "temporal":{"year_from":1334,"year_to":1630,"precision":"range","display":"۷۳۴–۱۰۳۹ ق",
             "confidence":"high","calendar":"islamic_lunar"},
 "geometry":{"type":"Point","coordinates":[48.2937,38.2513],"certainty":"exact"},
 "summary":"…","rank":88,"min_zoom":11,
 "relationships":[{"predicate":"located_in","label_fa":"واقع در","object":{"entity_type":"place","id":"…","label":"اردبیل"},"confidence":"high"}],
 "articles":[{"id":"art_…","slug":"…","title":"…","relation":"about"}],
 "sources":[{"id":"src_…","citation":"…"}],
 "disagreements":[{"topic":"سال آغاز ساخت","positions":[{"value":"۷۳۵ ق","evidence":[…],"confidence":"medium"}]}],
 "links":{"self":"…","map":"/?entity=plc_…&z=15&c=38.2513,48.2937&t=1500"} }
```
`links.map` مهم است: **اتصال دوطرفه Article ↔ Map** از همین‌جا می‌آید (بخش ۱۲ دست‌نوشته).

## ۸. `GET /api/v1/entities/{type}/{id}/related?depth=1&predicate=&limit=`
همسایگی گراف (برای پنل «کاوش» و گراف‌نمای بعدی).

## ۹. `GET /api/v1/articles` , `GET /api/v1/articles/{slug}`
```json
{"id":"art_…","slug":"rise-of-safavids","lang":"fa","status":"published","revision":5,
 "title":"ظهور صفویان","summary":"…","body_md":"…","published_at":"…",
 "reading_time_min":9,"entities":[{"entity_type":"political_entity","id":"pol_…","label":"صفویان","relation":"about"},…],
 "map_state":{"center":[38.25,48.29],"zoom":7,"time":{"from":1490,"to":1524},"layers":["places","political_entities","events"]},
 "sources":[…]}
```
`map_state` دقیقاً همان چیزی است که دکمه‌ی **«View on Map»** در مقاله استفاده می‌کند.

## ۱۰. `GET /api/v1/search`
```
?q=صفوی&locale=fa&types=place,person,event,article&limit=20&near=plc_…&radius_km=50&t=1500
```
```json
{"data":[{"id":"…","entity_type":"person","label":"شاه اسماعیل یکم","matched_on":"name",
          "score":0.91,"snippet":"…","t_display":"۸۹۲–۹۳۰ ق","href":"…"}],
 "page":{…},"meta":{"normalized_query":"صفوی","driver":"fixtures"}}
```

## ۱۱. `GET /api/v1/atlas/query` (جست‌وجوی ساخت‌یافته، نه متن)
«بناهای صفوی در شعاع ۲۰ کیلومتری اردبیل»:
```
?kind=building&period=safavid&near=plc_ardabil&radius_km=20&t=1550&mode=at
```
پاسخ = همان GeoJSON `/atlas/features` + `meta.applied_filters`.

## ۱۲. نسخه‌بندی و سازگاری
- افزودن فیلد/endpoint = minor (مجاز در v1).
- حذف/تغییر معنا/تغییر نوع = breaking → `v2` + doc migration + دوره‌ی overlap.
- CI یک snapshot از `openapi.json` دارد؛ diff غیرمنتظره → fail.
