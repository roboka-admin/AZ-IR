# نقشه‌ی راه (اصلاح‌شده)

> تفاوت‌ها با roadmap دست‌نوشته در `docs/00-design-review.md` بخش ۶ توضیح داده شده.
> **Definition of Done هر فاز:** کد + تست + migration + doc + fixture + «کاربر می‌تواند ببیندش».

| فاز | نام | خروجی | وضعیت |
|-----|-----|-------|-------|
| **0** | Walking Skeleton | monorepo · CI · یک مسیر عمودی نازک fixture→repo→service→API→map→timeline | ✅ انجام شد |
| **1** | Foundation | لایه‌بندی backend · error model RFC 9457 · logging · config · CORS · health · meta · lint/type/test · docker-compose · AGENTS.md · docs/ADR | ✅ انجام شد |
| **2** | Domain Model | schema کامل (place/person/event/political_entity/article/source/assertion/evidence/name_variant/taxonomy/period) + ERD + migrations | ✅ schema نوشته و در dev اعمال شد — منتظر review پژوهشی |
| **3** | Temporal & Calendar Engine | موتور تقویم (قمری/شمسی/جولیان/میلادی) · precision/fuzz · interval algebra · bitemporal · golden tests | ✅ هسته پیاده شد |
| **3.5** | Data Ingestion & Lint | فرمت fixture · ۱۳ قاعده‌ی lint · seed به Postgres · import از Wikidata (unverified) | 🔜 |
| **4** | Spatial | PostGIS adapter کامل · LOD · bbox/intersects/radius · this is the `postgis` driver (در CI با postgis image) | 🔜 |
| **5** | Historical API | `/atlas/features`, `/atlas/timeline`, `/atlas/context`, `/entities/*`, `/articles`, `/search`, `/atlas/query` · cursor pagination · ETag | 🔜 (اسکلت در فاز ۰/۱) |
| **6** | Editorial | auth · نقش‌ها · workflow · audit_log · revision snapshots · پنل ورود داده · lint UI | 🔜 |
| **7** | Map | MapLibre · basemap modern/historical · لایه‌ها · popup · disclaimer · fallback آفلاین | 🔜 |
| **8** | Timeline | temporal zoom · histogram · scrub local · play · سوییچ تقویم | 🔜 |
| **9** | Semantic Zoom | قرارداد rank/min_zoom · رفتار UI در ۵ سطح · L4 (اجزای بنا) | 🔜 |
| **10** | Entity ↔ Article | اتصال دوطرفه کامل · `map_state` مقاله · Related Articles در card | 🔜 |
| **11** | Advanced Explorer | لایه‌های people/routes/archaeology/battles · What Was Here? · Historical Density · disagreements UI | 🔜 |
| **12** | Stories / Compare / Performance | Story mode · Follow History · Compare (زمان و مکان) · PMTiles · CDN · clustering | 🔜 |

## ترتیب پیشنهادی اجرای واقعی (بعد از این commit)

1. **داده‌ی Pilot اردبیل** (۲۰ place، ۱۰ person، ۱۰ event، ۱۰ building، ۱۰ article، ۳۰ source) با منبع واقعی.
   این گلوگاه پروژه است، نه کد.
2. **PostGIS adapter + CI با postgis image** تا مسیر اصلی تست شود.
3. **Editorial پنل** (چون تیم پژوهشی بدون ابزار، داده وارد نمی‌کند).
4. بعد Map/Timeline polish و Semantic Zoom.

## معیارهای موفقیت (KPI)

- ۱۰۰٪ entity منتشرشده با ≥۱ منبع معتبر
- پوشش زمانی: هر سده از ۸۰۰ ق.م تا امروز حداقل یک رویداد ثبت‌شده در حوزه‌ی Pilot
- نسبت assertionهای `accepted` به `disputed` شفاف و گزارش‌شده
- p95 `/atlas/features` ≤ ۳۰۰ms
- time-to-first-insight کاربر جدید ≤ ۲۰ ثانیه (از ورود تا دیدن یک روایت معنادار)
