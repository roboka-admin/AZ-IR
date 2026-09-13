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
- رکورد `in_review` **قابل ویرایش نیست** (`EDITABLE_STATUSES` در `domain/editorial.py`): بازبین
  هرگز متنی را تأیید نمی‌کند که زیر دستش جابه‌جا شده باشد. برای ادامهٔ ویرایش، بازبین آن را با
  `request_changes` به draft برمی‌گرداند.
- `publish` فقط از `in_review` ممکن است، و فقط اگر gateهای lint پاک باشند (بخش ۳).

### هویت و نشست (پیاده‌سازی‌شده در ADR-0017)

| موضوع | تصمیم |
|-------|-------|
| رمز عبور | argon2id (`argon2-cffi`)؛ هرگز در log، پاسخ API یا مخزن |
| نشست | کوکی `azir_session` با `HttpOnly`؛ در DB فقط **sha256(token)** نگه داشته می‌شود |
| CSRF | کوکی خوانا `azir_csrf` + هدر `X-CSRF-Token` (double-submit) روی هر نوشتن |
| SameSite | `lax` به‌طور پیش‌فرض؛ وقتی دامنهٔ frontend جداست `none` + `secure` |
| throttle | ۵ تلاش ناموفق در ۵ دقیقه برای هر «ایمیل + IP» → `429` با `Retry-After` |
| حساب‌ها | `azir user add` (رمز از stdin، نه argv) و `azir user sync-dev` |
| خاموش‌کردن | `AZIR_EDITORIAL_ENABLED`؛ در `production` به‌طور پیش‌فرض **خاموش** است |
| audit | هر نوشتن، از جمله `login`/`login_failed`/`logout`، با `request_id` همان درخواست |

## ۳. Data Lint (در CI روی fixtureها، و قبل از publish در UI)

اجرا: `azir lint` روی کل پیکره یا یک رکورد، `GET|POST /api/v1/editorial/lint` در پنل، و
**gate قبل از publish** در `services/editorial.py`. خروجی یک گزارش JSON است که همیشه
`not_evaluated` را هم دارد — قاعده‌ای که ماشین بررسی نمی‌کند **پنهان نمی‌شود**، اعلام می‌شود.

| # | قاعده | سطح | وضعیت اجرا |
|---|-------|-----|------------|
| D1 | `death_year_from >= birth_year_from` | error | ⏳ ماشینی نشده: تولد/مرگ در `assertion` است و به query claim‑شکل نیاز دارد |
| D2 | هر هندسه داخل `corpus_bbox`؛ داخل `study_area_bbox` بهتر است | error / warn | ✅ دو حلقه: بیرونِ پیکره = error؛ بیرونِ حوزهٔ مطالعه ولی داخل پیکره = warn («زمینه»، مثل قزوین) |
| D3 | هر claim پذیرفته‌شده evidence کافی دارد | error | ✅ `stance ∈ {supports, qualifies}` کافی است؛ فقط روی edgeهایی که `is_claim` (لینک ساختاری claim نیست) |
| D4 | هر entity با `status=published` ≥ ۱ source مرتبط | error | ✅ gate انتشار؛ در پنل هم به editor نشان داده می‌شود |
| D5 | هر entity منتشرشده ≥ ۱ `name_variant(kind=preferred)` در `fa` | error | ✅ gate انتشار + هنگام create/patch (نام فارسی اجباری است) |
| D6 | `place_link(contains)` بدون دور | error | ⏳ تستِ fixtureها دور را می‌گیرد؛ بررسی per-record نوشته نشده |
| D7 | همه‌ی `kind`/`predicate`/`locator_type` از taxonomy | error | ⏳ نسبی: enumها (`status`, `precision`, …) در دامنه اجباری‌اند؛ کدهای آزاد `kind`/`predicate` هنوز مقابل taxonomy چک نمی‌شوند |
| D8 | تاریخ‌های `unknown` باید `confidence ∈ {low, disputed}` داشته باشند | warn | ✅ دو لایه: `TemporalInterval.__post_init__` خودش تنزل می‌دهد، lint هم گزارش می‌کند |
| D9 | entity بدون geometry → باید `coverage_note` داشته باشد | warn | ✅ (`article` معاف است: متن، feature نیست) |
| D10 | دو entity با نام یکسان در یک مکان → احتمال تکراری | warn | ⏳ به index شباهت نام (pg_trgm) روی کل پیکره نیاز دارد |
| D11 | رویداد با `attestation=legendary` باید `confidence=low` | error | ✅ gate انتشار |
| D12 | منبع بدون سال/نویسنده/ناشر → کتاب‌شناسی ناقص | warn | ⏳ روی fixture منبع‌ها تست شده، نه per-entity |
| D13 | assertionهای متضاد accepted → باید `disputed` شوند | error | ⏳ نیمه: هنگام `disputed` کردن، نوشتنِ `topic_fa` **اجباری** است؛ تشخیص خودکار تضاد به گروه‌بندی topic در read model نیاز دارد |

قاعدهٔ عملیاتی: `make lint-data` باید با **صفر error** تمام شود (پیکرهٔ seed امروز ۰ error و
۲ warning دارد — هر دو رکوردهای «زمینه»ی قزوین). چیزی که CI نگذارد، در `lint_run` هم ثبت می‌شود
تا روندِ کیفیت داده در زمان دیده شود.

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
