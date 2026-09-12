# گردش‌کار ویرایش، منابع و کیفیت داده

> مرجع: ADR-0010 (workflow/roles), ADR-0013 (licensing), ADR-0003 (evidence)

## ۱. زنجیره‌ی استناد (همان بخش ۱۵ دست‌نوشته، دقیق‌تر)

```
Assertion  ──(1..n)──  Evidence  ──(n..1)──  Source  ──(1..n)──  Edition/Version
     │                     │
     │                     └── locator: {type, value} + quote_original + quote_translation + stance
     └── subject/predicate/object + valid time + confidence + status
```

`locator_type ∈ page|folio|chapter|verse|paragraph|line|entry|catalogue_number|map_sheet|plate|inscription_field`

**Source vs Edition:** یک «تاریخ وصاف» یک `source` است؛ چاپ ایران ۱۳۳۸ و ترجمه‌ی انگلیسی ۱۹۷۱ دو
`edition` از آن هستند. استناد همیشه به edition + locator است، نه به «کتاب» کلی.
→ `source_edition(source_id, kind, editor, translator, publisher, place, year, isbn, url, is_canonical)`

## ۲. گردش‌کار

```
        ┌──────────┐  submit   ┌───────────┐  approve   ┌───────────┐  archive  ┌──────────┐
draft ──┤          ├──────────►│ in_review ├───────────►│ published ├──────────►│ archived │
        └────▲─────┘           └─────┬─────┘            └─────┬─────┘           └──────────┘
             │   changes_requested   │                        │ new revision (draft copy)
             └───────────────────────┘◄───────────────────────┘
```
- `editor` می‌سازد/ویرایش می‌کند → `proposed`/`draft`.
- `reviewer|admin` تأیید می‌کند → `accepted`/`published`.
- ویرایش entity منتشرشده یک **کپی draft با revision جدید** می‌سازد؛ نسخه‌ی عمومی دست‌نخورده می‌ماند.
- هر تغییر: `audit_log` + `revision_snapshot`.

## ۳. Data Lint (در CI روی fixtureها، و قبل از publish در UI)

| # | قاعده | سطح |
|---|-------|-----|
| D1 | `death_year_from >= birth_year_from` | error |
| D2 | هر هندسه داخل `study_area.bbox` (مگر `modern_admin` یا flag صریح) | error |
| D3 | هر `assertion.status=accepted` ≥ ۱ evidence با `stance=supports` | error |
| D4 | هر entity با `status=published` ≥ ۱ source مرتبط | error |
| D5 | هر entity منتشرشده ≥ ۱ `name_variant(kind=preferred)` در `fa` | error |
| D6 | `place_link(contains)` بدون دور | error |
| D7 | همه‌ی `kind`/`predicate`/`locator_type` از taxonomy | error |
| D8 | تاریخ‌های `unknown` باید `confidence ∈ {low, disputed}` داشته باشند | warn |
| D9 | entity بدون geometry → در نقشه دیده نمی‌شود؛ باید `coverage_note` داشته باشد | warn |
| D10 | دو entity با نام یکسان در یک مکان → احتمال تکراری (identity review) | warn |
| D11 | رویداد با `attestation=legendary` باید `confidence=low` و برچسب UI داشته باشد | error |
| D12 | منبع بدون سال/نویسنده/ناشر → کتاب‌شناسی ناقص | warn |
| D13 | assertionهای متضاد accepted → باید `status=disputed` شوند | error |

خروجی lint یک گزارش JSON است که در پنل editorial هم نشان داده می‌شود (نه فقط CI).

## ۴. Ingestion (چون گلوگاه واقعی پروژه، ورود داده است)

مسیرهای ورود:
1. **دستی** از پنل editorial (برای داده‌ی پژوهشی دقیق).
2. **fixture YAML** در repo (برای bulk اولیه؛ همان چیزی که CI و dev driver می‌خوانند).
3. **import از منابع ساخت‌یافته** با provenance و وضعیت `imported_unverified`:
   - Wikidata/DBpedia (QID در `source.external_ids` ذخیره می‌شود)
   - فهرست آثار ملی ایران / پرونده‌ی یونسکو (برای بناها)
   - OSM (فقط برای هندسه‌ی معاصر و به‌عنوان لایه‌ی `modern_admin`، با ذکر © OpenStreetMap)
   > داده‌ی importشده **هرگز** مستقیماً `published` نمی‌شود؛ باید یک پژوهشگر آن را `verified` کند.

## ۵. فرمت fixture (تنها منبع seed)

```yaml
# backend/seeds/fixtures/places.yaml
places:
  - id: plc_sheikh_safi_complex      # id پایدار و خوانا برای fixture (در prod به UUIDv7 نگاشت می‌شود)
    kind: building
    slug: sheikh-safi-complex
    status: published
    importance: 0.95
    temporal: {calendar: islamic_lunar, from: "734", to: "1039", precision: range, confidence: high}
    geometry: {kind: footprint, certainty: exact, type: Point, coordinates: [48.2937, 38.2513]}
    names:
      - {lang: fa, form: "بقعهٔ شیخ صفی‌الدین اردبیلی", kind: preferred}
      - {lang: en, form: "Sheikh Safi al-Din Khānegāh and Shrine Ensemble", kind: preferred, transliteration_system: Common}
    sources: [src_unesco_2010, src_blair_bloom]
```
هم `scripts/seed_postgres.py` و هم `fixtures` driver همین فایل‌ها را می‌خوانند → **یک منبع حقیقت برای seed**.

## ۶. پوشش داده (coverage honesty)

هر پاسخ API یک `meta.coverage` دارد: چه تعداد entity در این viewport/بازه زمانی هست و چه بخشی
`draft`/بدون منبع است. UI «خالی بودن» را به‌عنوان «داده نداریم» نشان می‌دهد، نه «اتفاقی نیفتاده».
