# مستندات پروژه

ترتیب خواندن پیشنهادی:

1. [`00-design-review.md`](00-design-review.md) — **بازنگری طراحی**: ۱۰ حفره‌ی بحرانی و راه‌حل‌ها (اول این را بخوانید)
2. [`01-architecture.md`](01-architecture.md) — معماری، لایه‌ها، جریان درخواست، استقرار
3. [`02-domain-model.md`](02-domain-model.md) — ERD، کاتالوگ جدول‌ها، invariantها
4. [`03-temporal-and-calendar.md`](03-temporal-and-calendar.md) — موتور تقویم و عدم‌قطعیت زمانی
5. [`04-spatial-and-semantic-zoom.md`](04-spatial-and-semantic-zoom.md) — PostGIS، LOD، فرمول rank
6. [`05-api-contract.md`](05-api-contract.md) — قرارداد API v1 (مرجع frontend)
7. [`06-map-and-frontend.md`](06-map-and-frontend.md) — UX نقشه، تایم‌لاین، a11y، زبان بصری عدم‌قطعیت
8. [`07-editorial-and-data-quality.md`](07-editorial-and-data-quality.md) — گردش‌کار ویرایش، Data Lint، Ingestion
9. [`08-performance-and-scale.md`](08-performance-and-scale.md) — SLO، بودجه‌ی payload، cache
10. [`09-roadmap.md`](09-roadmap.md) — نقشه‌ی راه اصلاح‌شده و KPI

## تصمیم‌های معماری

همه در [`adr/`](adr/) — از ADR-0001 تا ADR-0018. ADRها ویرایش نمی‌شوند، supersede می‌شوند.

| ADR | موضوع |
|-----|-------|
| 0001 | ثبت تصمیم‌ها به‌صورت ADR |
| 0002 | Modular monolith + PostgreSQL/PostGIS |
| 0003 | گراف assertion-محور و bitemporal |
| 0004 | یک `Place` با kind + facet؛ War = Event |
| 0005 | موتور تقویم و بازنمایی دوگانه‌ی زمان |
| 0006 | نام‌های چندزبانه/چندنوشتاری |
| 0007 | نرمال‌سازی و جست‌وجوی فارسی |
| 0008 | UUIDv7 + slug + نسخه‌ی قابل استناد |
| 0009 | قرارداد API v1 |
| 0010 | گردش‌کار ویرایش و نقش‌ها |
| 0011 | تحویل داده‌ی نقشه: GeoJSON → PMTiles |
| 0012 | دوره‌بندی منطقه‌آگاه و واژگان کنترل‌شده |
| 0013 | لایسنس داده و سیاست نمایش مرزها |
| 0014 | پورت‌پذیری: PostGIS در prod، fixtures در dev |
| 0015 | فرانت‌اند: URL منبع حقیقتِ نما، basemap قابل‌کاوش، i18n درون‌سازمانی |
| 0016 | PostGIS: seed از loader، SQL صریح مکانی، تست‌های drift |
| 0017 | لایهٔ نوشتن: نشست، بازبینیِ کپی‌شده، gate انتشار |
| 0018 | خط تولید tile: PMTiles دست‌ساز، تحویل دوگانه، زمان سمت کلاینت |

قوانین اجرایی برای هر مشارکت‌کننده (انسان یا agent) در [`../AGENTS.md`](../AGENTS.md).
