# بازنگری طراحی — اطلس تاریخی تعاملی آذربایجان ایران

> **Status:** Accepted (v1) — 2026-09-12
> **نویسنده:** Senior engineering review
> **ورودی:** سند Handoff پروژه (بخش‌های ۱ تا ۳۲)
> **هدف:** قبل از نوشتن کد، حفره‌های مدل داده و معماری را پیدا و رفع کنیم. هر مورد اینجا یا به یک ADR ختم شده یا به یک تسک در Roadmap.

---

## ۰. خلاصه مدیریتی (اگر فقط یک صفحه می‌خوانید)

ایده‌ی اصلی **درست است** و هسته‌ی آن را تغییر نمی‌دهیم:

- «نقشه + زمان» به‌عنوان رابط اصلی کشف ✅
- داده‌ی تاریخی مستقل از مقاله؛ مقاله = view روی داده ✅
- PostgreSQL + PostGIS به‌جای Neo4j در شروع ✅
- GeoJSON اول، Vector Tiles بعداً ✅
- لایه‌بندی Router → Service → Repository ✅
- اردبیل به‌عنوان Pilot ✅
- مرز امروزی ≠ مرز تاریخی ✅

اما **۱۰ حفره‌ی بحرانی** در مدل داده وجود دارد که اگر در Phase 2 اصلاح نشوند، بعداً با migration‌های دردناک و از دست رفتن اعتماد به داده مواجه می‌شویم:

| # | حفره | چرا بحرانی است | راه‌حل |
|---|------|----------------|--------|
| 1 | **تقویم‌ها (Calendar) کاملاً غایب‌اند** | منابع ما هجری قمری/شمسی/جولیان هستند. سال ۸۶۷ قمری = ۱۴۶۲–۱۴۶۳ میلادی. مدل `exact/circa/century` بدون calendar بی‌معناست | ADR-0005: هر زمان = (calendar, original, precision, confidence) + نرمال‌سازی به سال نجومی برای query |
| 2 | **مدل bitemporal نیست** | داده‌ی پژوهشی مدام اصلاح می‌شود؛ بدون «زمان ثبت/تغییر» نمی‌توانیم بگوییم «در سال ۲۰۲۵ چه باور داشتیم» | ADR-0003: valid-time + transaction-time + audit log |
| 3 | **Claim و Relation دو منبع حقیقت‌اند** | `born_in` هم یک relation است هم یک claim → تکرار و ناسازگاری | ADR-0003: Assertion-centric graph؛ هر یال = یک assertion قابل استناد |
| 4 | **هندسه بُعد زمانی و منبع ندارد** | مرز قراقویونلو در ۱۴۳۰ ≠ ۱۴۵۰؛ یک polygon کافی نیست | ADR-0004/0011: `temporal_geometry` (چند هندسه با بازه‌ی اعتبار + منبع + دقت) |
| 5 | **تناقض `Place` vs `Building`** | در handoff هر دو هستند → entity explosion | ADR-0004: یک `Place` با `kind` + جدول‌های facet |
| 6 | **مدل نام/زبان/نوشتار غایب است** | تبریز / تیریز / Tawrēz / Təbriz / タ… و نام‌های متفاوت در دوره‌های مختلف؛ بدون این، search و نمایش خراب است | ADR-0006: `name_variant` با language, script, temporal, source, preferred |
| 7 | **`Event` مدل‌سازی ناقص** | Event بازه دارد نه instant؛ مکان چندگانه با نقش؛ War/Campaign/Battle سلسله‌مراتب‌اند | ADR-0004: Event با `part_of` → War یک Event است، نه entity جدا |
| 8 | **`HistoricalPeriod` یک tag ساده فرض شده** | دوره‌بندی آذربایجان ≠ دوره‌بندی کل ایران؛ چند طرح رقیب وجود دارد | ADR-0012: `period_scheme` + period منطقه‌آگاه |
| 9 | **Search برای فارسی/عربی طراحی نشده** | FTS پیش‌فرض Postgres روی فارسی کار نمی‌کند (ی/ي، ک/ك، نیم‌فاصله، اعراب، ة/ه) | ADR-0007: normalization + pg_trgm + ستون `search_text` از روز اول |
| 10 | **ID/Slug/URL پایدار تعریف نشده** | لینک پژوهشی که بشکند = مرگ اعتبار سایت | ADR-0008: UUIDv7 پایدار + slug قابل تغییر + redirect + نسخه‌ی قابل استناد |

سه مورد **ریسک محصولی** هم هست که فنی نیستند ولی باید همین الان تصمیم بگیریم: سیاست مرزهای حساس (ADR-0013)، لایسنس داده و منبع‌دهی، و دسترسی‌پذیری (a11y) برای نقشه.

---

## ۱. آنچه درست است و دست نمی‌زنیم

این‌ها را صراحتاً تأیید می‌کنیم تا در بازنگری‌های بعدی دوباره زیر سؤال نروند:

1. **Map یک Interface است، نه یک تصویر.** نتیجه‌ی عملی: هیچ منطقی در MapLibre نیست؛ فقط renderer.
2. **Article منبع حقیقت نیست.** نتیجه‌ی عملی: هیچ فکت تاریخی فقط داخل متن مقاله وجود ندارد؛ هر فکت باید یک entity/assertion باشد و مقاله به آن لینک شود.
3. **PostGIS قبل از Graph DB.** نتیجه‌ی عملی: گراف با جدول‌های رابطه‌ای بیان می‌شود؛ اگر روزی لازم شد، assertion store به‌راحتی به RDF/property-graph export می‌شود (چون مدل triple است).
4. **Semantic Zoom یک قرارداد داده است، نه یک ترفند UI.** نتیجه‌ی عملی: هر feature یک `rank` و `min_zoom` دارد که از داده مشتق و مستند می‌شود.
5. **Pilot کوچک و واقعی بهتر از بزرگ و جعلی.** نتیجه‌ی عملی: قانون «Never fabricate» به‌صورت machine-checkable در data lint پیاده می‌شود.

---

## ۲. حفره‌های بحرانی (با جزئیات)

### ۲.۱ تقویم: بزرگ‌ترین حفره‌ی پنهان

دست‌نوشته‌ی فعلی این‌ها را دارد: `exact | circa | before | after | century | approximate | unknown`. این **دقت** را مدل می‌کند اما **تقویم** را نه. در عمل:

- منبع می‌گوید: «وفات شیخ صفی‌الدین در ۷۳۵ قمری». ۷۳۵ ه‍.ق ≈ ۱۳۳۴–۱۳۳۵ میلادی (چون سال قمری ~۳۵۴ روز است و ~۱۱ روز در سال جابه‌جا می‌شود).
- منابع دوره‌ی ایلخانی گاهی با تقویم حیوانی ترک-مغول یا تاریخ جلوس پادشاهان آمده‌اند.
- برای قبل از ۱۵۸۲ تفاوت جولیان/گرگورین تا ۱۰ روز است؛ برای رویدادهای کوتاه (مثل یک محاصره) این یعنی اشتباه در ترتیب رویدادها.
- تبدیل هجری قمری → میلادی **یک‌به‌یک نیست**: یک سال قمری تقریباً همیشه روی دو سال میلادی می‌افتد.

**نتیجه‌ی معماری:** هر نقطه/بازه‌ی زمانی باید **دو بازنمایی** داشته باشد:

```text
1) Faithful representation  → آنچه منبع گفته (تقویم، متن اصلی، دقت، اطمینان)
2) Normalized interval      → بازه‌ی قابل مقایسه برای query و index
                              (سال نجومی: year 0 = 1 BCE, year -1 = 2 BCE)
```

و conversion **یک تابع خالص، تست‌شده و نسخه‌دار** است، نه یک عدد ذخیره‌شده. یعنی: هیچ‌وقت `year = 1400` را برای «circa 1400» ذخیره نمی‌کنیم؛ ذخیره می‌کنیم:

```text
calendar = islamic_lunar? gregorian?  → در این مثال: gregorian
display  = "circa 1400"
precision = circa_year
norm_from = 1395, norm_to = 1405     (fuzz از precision مشتق می‌شود)
confidence = medium
source   = [EIr, “Ardabil”]
```

👈 ADR-0005 + `azir.domain.temporal` (پیاده‌سازی شده در همین Phase).

### ۲.۲ Bitemporal: «چه وقتی درست بود» در برابر «چه وقتی ما این را باور داشتیم»

داده‌ی تاریخی یک snapshot نیست؛ یک فرایند پژوهشی است. سه نوع بازه باید جدا شوند:

| نوع | مثال | ستون‌ها |
|-----|------|---------|
| **Existence** (وجود موجودیت) | مسجد از ۱۴۵۰ تا ۱۸۰۰ پابرجا بود | `valid_from`, `valid_to` روی entity |
| **Assertion validity** (اعتبار یک رابطه/صفت) | تبریز پایتخت ایلخانان بود: ۱۲۶۵–۱۳۳۵ | روی `assertion` |
| **Transaction time** (زمان دانش ما) | ما این را در ۲۰۲۶-۰۳ ثبت کردیم، در ۲۰۲۶-۰۸ اصلاح شد | `asserted_at`, `superseded_at`, `audit_log` |

بدون لایه‌ی سوم، وقتی یک پژوهشگر داده را اصلاح می‌کند، تاریخ قبلی گم می‌شود و نمی‌توانیم diff/review/rollback داشته باشیم — که برای تیم کوچک پژوهشی (انتخاب شما) حیاتی است.

👈 ADR-0003 + ADR-0010.

### ۲.۳ Assertion-centric graph: ادغام Claim و Relation

در دست‌نوشته، دو مسیر موازی وجود دارد:

```text
Person --born_in--> Place        (relationship table)
Claim: "Shah Ismail born_in Ardabil" --Evidence--> Source
```

این یعنی هر فکت **دو بار** ذخیره می‌شود و ممکن است از هم واگرا شوند. راه‌حل: یال‌های گراف را **reify** کنیم:

```text
assertion(id, subject, predicate, object,
          valid_from, valid_to, precision, calendar,
          confidence, status,            -- proposed|accepted|rejected|disputed
          asserted_by, asserted_at, superseded_at)

evidence(assertion_id, source_id, locator, quote_original, quote_translation, stance)
                                        -- stance: supports|contradicts|qualifies
```

و ستون‌های روی خود entity (مثلاً `person.birth_place_id`) یک **projection/materialized view** از assertion‌های `accepted` با بالاترین confidence است. یعنی:

> **منبع حقیقت = assertion store. entity = نمای تجمیع‌شده‌ی «اجماع فعلی».**

مزایا:
- اختلاف تاریخی به‌جای حذف، به‌صورت دو assertion با `status=disputed` زندگی می‌کند.
- هر فکت روی نقشه/تایم‌لاین می‌تواند منبع‌دار باشد (کلیک → «چرا این را می‌گوییم؟»).
- export به RDF/Wikidata trivial است (triple + provenance).
- رابطه‌ها خودشان بازه‌ی زمانی دارند (حکم‌رانی ۱۵۰۱–۱۵۲۴) بدون جدول جدا برای هر نوع رابطه.

هزینه: query کمی سنگین‌تر است → با materialized projection و ایندکس‌های مناسب حل می‌شود (ADR-0003).

### ۲.۴ هندسه‌ی زمان‌دار + عدم‌قطعیت مکانی

مشکلات مدل «یک Place = یک geometry»:

1. مرزها در زمان تغییر می‌کنند → یک حکومت می‌تواند **چند** هندسه با بازه‌ی اعتبار داشته باشد.
2. مکان تقریبی است: «نبرد در حوالی چالدران» ≠ نقطه‌ی دقیق → باید `spatial_certainty: exact|approximate|uncertain` و حتی «polygon of uncertainty» داشته باشیم.
3. Provenance هندسه: چه کسی digitize کرده؟ از کدام نقشه/منبع؟ مقیاس چقدر؟ (برای داده‌ی پژوهشی الزامی است)
4. **مرز امروزی ≠ مرز تاریخی**: باید `geometry.kind = modern_admin | historical_extent | site_footprint | route_alignment | approximate_locus` باشد و UI به‌صورت پیش‌فرض مرزهای امروزی را به‌عنوان لایه‌ی جداگانه و کم‌رنگ نشان دهد.
5. Semantic zoom نیاز به LOD دارد: polygon ساده‌شده برای zoom پایین (`ST_SimplifyPreserveTopology` یا چند نسخه‌ی از پیش محاسبه‌شده).

👈 `place_geometry(place_id, kind, geom, valid_from, valid_to, lod_zoom_min/max, source_id, certainty, digitized_by)`.

### ۲.۵ `Place` vs `Building` vs `ArchaeologicalSite` — یکی یا چندتا؟

دست‌نوشته هم `Place` را با انواع Building/Castle/Mosque/Bazaar/… تعریف کرده، هم `Building` را در MVP به‌عنوان entity مستقل آورده. این تناقض باید همین الان حل شود، چون روی API و URL و graph اثر دارد.

**تصمیم (ADR-0004): یک موجودیت `Place` با `kind` سلسله‌مراتبی + جدول‌های facet.**

```text
place(id, kind, ...)                        kind ∈ region|city|village|district|neighborhood
                                                 |site|building|castle|mosque|bazaar|bridge
                                                 |cemetery|mountain|river|archaeological_site|route|...
building_facet(place_id, construction_technique, floor_count, ... )
archaeological_site_facet(place_id, excavation_years, cultural_layers, ...)
route_facet(place_id, route_kind, from_place_id, to_place_id, ...)
```

دلیل:
- گراف یکنواخت می‌ماند (همه‌چیز `place_id` است) → assertion model ساده می‌شود.
- API/URL پایدار: `/entity/place/{id}` با `kind` به‌عنوان فیلتر، نه ۱۵ endpoint مختلف.
- افزودن نوع جدید = افزودن مقدار enum + facet اختیاری، نه migration ساختاری.
- «Building» در UI یک **layer** است (فیلتر `kind`), نه یک entity type متفاوت.

### ۲.۶ نام‌ها، زبان‌ها، نوشتارها

برای پروژه‌ای درباره‌ی آذربایجان این یک نیاز درجه‌یک است، نه nice-to-have:

- نوشتارهای چندگانه: فارسی (عربی)، ترکی آذربایجانی (لاتین و عربی)، انگلیسی، عربی، ارمنی، گرجی، روسی، عثمانی.
- نام‌های تاریخی: «توریژ/تاوریز» در منابع سریانی، «Tabrīz» در عربی، «Tauris» در لاتین اروپایی.
- نام‌ها در زمان عوض می‌شوند: ارومیه / رضائیه (۱۳۵۸–۱۳۵۹ ش) / اورمیه.
- transliteration policy نیاز دارد (کدام سیستم؟ DMG؟ EI3؟) وگرنه هر پژوهشگر یک‌جور می‌نویسد.

**تصمیم (ADR-0006):**

```text
name_variant(id, entity_type, entity_id, lang, script, form,
             is_preferred, valid_from, valid_to, source_id, note)
```

+ یک تابع «resolve display name(entity, locale, at_time)» با قواعد صریح و تست‌شده.
+ ستون `search_text` نرمال‌شده (فارسی/عربی normalize شده) برای جست‌وجو.

### ۲.۷ `Event` — از «نقطه در زمان» به «فرایند در زمان و مکان»

نقص‌های مدل فعلی:

1. Event **بازه** دارد (محاصره‌ی ۶ ماهه) نه instant.
2. یک رویداد می‌تواند **چند مکان** با نقش داشته باشد (نبرد چالدران: میدان نبرد، اردوگاه، مسیر عقب‌نشینی).
3. **سلسله‌مراتب**: Battle ⊂ Campaign ⊂ War. → پاسخ سؤال دست‌نوشته: **War نباید entity مستقل باشد**؛ War یک `event(kind=war)` است و بقیه با `part_of` به آن وصل می‌شوند. این تصمیم مدل را کوچک‌تر و منسجم‌تر می‌کند.
4. **علّیت**: `caused_by` / `led_to` (قتل شاه‌اسماعیل؟ نه — مثال: محاصره‌ی تبریز ← قحطی ← مهاجرت).
5. **Attestation**: برخی رویدادها افسانه‌ای/منبع‌ضعیف‌اند → باید `attestation: well_attested|single_source|traditional|legendary` داشته باشند و در UI متفاوت نمایش داده شوند.
6. **Typology کنترل‌شده**: battle|siege|treaty|founding|destruction|migration|revolt|coronation|earthquake|epidemic|construction|publication|… با منبع برای خودِ تایپولوژی (دسته‌بندی هم یک انتخاب علمی است).

### ۲.۸ `HistoricalPeriod` باید منطقه‌آگاه و چندطرحی باشد

«دوره‌ی صفوی» در آذربایجان معنای متفاوتی با «دوره‌ی صفوی» در گجرات دارد؛ و «دوران باستان» یک برچسب مبهم است. همچنین چند طرح دوره‌بندی مشروع وجود دارد (باستان‌شناسانه، سیاسی-دودمانی، اقتصادی).

**تصمیم (ADR-0012):**

```text
period_scheme(id, name, description, source_id)      -- e.g. "Azerbaijan political chronology v1"
period(id, scheme_id, code, parent_id, applies_to_place_id, valid_from, valid_to, source_id)
```

و membership یک موجودیت در period هم **مشتق از تاریخ** است و هم می‌تواند **صریحاً tag** شود (وقتی تاریخ نامعلوم است). هر دو باید در UI قابل تفکیک باشند.

### ۲.۹ جست‌وجوی فارسی/عربی

FTS انگلیسی Postgres (tsvector + to_tsquery) برای فارسی عملاً بی‌فایده است: tokenizer بر اساس فاصله کار می‌کند، نیم‌فاصله (U+200C) را نمی‌شناسد، و هیچ stemmer/dictionary فارسی ندارد.

**تصمیم (ADR-0007):**
1. تابع `normalize_fa(text)`: ی→ي، ک→ك، ة→ه، أ/إ/آ→ا، حذف اعراب، حذف ZWNJ/نیم‌فاصله به فاصله، یکسان‌سازی ارقام عربی/فارسی→لاتین، حذف «ـ» کشیده.
2. ستون `search_text` (نرمال‌شده) روی هر entity، نگهداری‌شده با trigger یا در service layer.
3. ایندکس: `GIN (search_text gin_trgm_ops)` + `pg_trgm` برای fuzzy؛ بعداً اگر لازم شد Meilisearch/Typesense.
4. Alias/name_variant همه داخل search_text می‌روند.
5. **Query ترکیبی** («بناهای صفوی اطراف اردبیل») یک search نیست؛ یک `atlas query` است → endpoint جدا با پارامترهای ساخت‌یافته (`kind`, `period`, `near`, `radius_km`).

### ۲.۱۰ شناسه، slug، URL، نسخه

- `id`: **UUIDv7** (زمان‌مرتب، index-friendly) با prefix خوانا برای انسان: `plc_01J…`, `per_01J…`, `evt_01J…`.
- `slug`: خوانا و قابل تغییر (`sheikh-safi-complex`) + جدول `slug_redirect` تا لینک‌های قدیمی نشکنند.
- **نسخه‌ی قابل استناد**: هر entity یک `revision` عددی دارد؛ URL `…/place/plc_x?v=12` یک snapshot قابل ارجاع در مقاله‌ی علمی می‌دهد. این برای اعتبار پژوهشی سایت لازم است.
- سیاست: هیچ‌وقت entity حذف سخت (hard delete) نمی‌شود؛ `status=archived` + tombstone.

---

## ۳. حفره‌های مهم (درجه‌دوم ولی باید الان در schema دیده شوند)

| # | موضوع | تصمیم |
|---|-------|-------|
| 11 | **کاربر/نقش/بازبینی/audit** (تیم ۲–۵ نفره) | `user`, `role ∈ admin|reviewer|editor|contributor`, workflow `draft→in_review→published→archived`, `audit_log` روی همه‌ی writeها — ADR-0010 |
| 12 | **لایسنس و سیاست حقوقی** | کد: MIT؛ **داده: CC BY-SA 4.0** (تا با Wikidata/OSM سازگار بماند و استناد اجباری شود)؛ منابع: فقط ارجاع، نه بازنشر متن کامل دارای حق نشر — ADR-0013 |
| 13 | **سیاست مرزهای حساس** | مرزهای معاصر = لایه‌ی جدا با source «اداری رسمی»، مرزهای تاریخی = «بازسازی پژوهشی با عدم‌قطعیت». disclaimer دائمی روی نقشه. هیچ‌وقت یک مرز تاریخی را قطعی نشان نمی‌دهیم مگر منبع قوی داشته باشد — ADR-0013 |
| 14 | **قرارداد API دقیق** | cursor pagination, sparse fieldsets, RFC 9457 error, ETag + `Cache-Control`, rate limit, `/api/v1` با contract test — ADR-0009 |
| 15 | **`time` در API یک instant نیست** | `?t=1450` مبهم است → `?t=1450&mode=at` یا `?from=1400&to=1500&mode=overlaps`، به‌علاوه‌ی `cal=` برای تقویم ورودی |
| 16 | **basemap/tiles برای PaaS** | **PMTiles** (یک فایل، self-contained، روی S3/R2/CDN یا حتی static hosting) + style سفارشی؛ fallback کامل آفلاین در frontend تا اگر CDN در دسترس نبود نقشه خراب نشود — ADR-0011 |
| 17 | **SLO و مدل مقیاس** | هدف Phase-1..5: ≤ 50k place، ≤ 5k feature در viewport، p95 ≤ 300ms برای `/atlas/features`، payload ≤ 512KB (degrade با field budget) — doc 12 |
| 18 | **a11y و RTL** | نقشه ذاتاً برای screen reader بسته است → **نمای فهرستی موازی** («List view» همان query)، keyboard navigation روی featureها، `lang`/`dir` درست، سیاست ارقام و تاریخ نمایشی — doc 08 |
| 19 | **Data lint** | اعتبارسنجی ماشینی: مرگ > تولد، هندسه داخل bbox منطقه‌ی مورد مطالعه، هر claim حداقل یک evidence، هیچ entity بدون source منتشر نشود، نام تکراری در یک مکان → CI روی fixtureها |
| 20 | **نمایش عدم‌قطعیت در UX** | زبان بصری مشترک: خط‌چین = مرز تقریبی، هاله = مکان نامطمئن، علامت ⚖ = مورد اختلاف، رنگ کم‌رنگ = منبع ضعیف. باید یک design token باشد نه تصمیم هر کامپوننت |
| 21 | **Cache برای query زمان‌دار** | کلید cache با `time` خام = انفجار cache → زمان را به **bucket** گرد می‌کنیم (bucket اندازه‌اش از zoom/سطح تایم‌لاین مشتق می‌شود) |
| 22 | **Route** | مسیر یک LineString ساده نیست: `route` + `route_segment(seq, from_place, to_place, geometry, valid_from/to)`؛ مسیرها در زمان جابه‌جا می‌شوند |
| 23 | **Identity resolution اشخاص** | یک نفر با چند نام/انتساب در منابع → `person_same_as(candidate, confidence, source)` + صفحه‌ی disambiguation |
| 24 | **داده‌های حساس (قومیت/مذهب/زبان)** | اینها **فکت نیستند، گزارش منبع‌اند** → فقط به‌صورت assertion با source ذخیره شوند، نه ستون مستقیم روی person |
| 25 | **دسترسی کاربران داخل ایران** | CDN/فونت/tile باید fallback محلی داشته باشند؛ فونت Vazirmatn را **self-host** می‌کنیم (نه Google Fonts) و tileها را می‌توان روی همان دامنه سرو کرد |

---

## ۴. ریسک‌های محصولی/اجرایی (غیرفنی ولی تعیین‌کننده)

1. **Scope بزرگ است (۱۲ فاز).** خطر: ۶ ماه زیرساخت بسازیم و هنوز کاربر چیزی نبیند.
   → **Walking Skeleton اول**: یک مسیر عمودی نازک (fixture → repo → service → API → map → timeline) که از روز ۱ کار می‌کند، بعد فازها آن را ضخیم می‌کنند. (در همین Phase انجام شد.)
2. **گلوگاه واقعی، ورود داده‌ی پژوهشی است نه کد.** ۲۰ place + ۳۰ source با کیفیت، هفته‌ها کار انسانی است.
   → ابزار ingestion از منابع ساخت‌یافته (Wikidata، فهرست آثار ملی، یونسکو) با `status=imported_unverified` و provenance؛ یعنی «وارد کردن» و «تأیید کردن» دو کار جدا باشند.
3. **تعریف موفقیت نداریم.** پیشنهاد KPI:
   - تعداد entity با ≥۱ source معتبر (هدف Pilot: ۱۰۰٪)
   - درصد assertionهای accepted vs disputed
   - زمان تا اولین تعامل معنادار کاربر روی نقشه (time-to-first-insight)
   - پوشش زمانی: چند درصد از بازه‌ی ۸۰۰ ق.م تا امروز حداقل یک رویداد ثبت‌شده دارد
4. **ریسک سیاسی/روایتی.** پروژه‌ای درباره‌ی آذربایجان با روایت‌های رقیب ملی/منطقه‌ای.
   → سیاست صریح: ما **ادعاها را با منبع** نمایش می‌دهیم، نه یک روایت رسمی. disputed پنهان نمی‌شود. (همان قانون ۱۵ دست‌نوشته، ولی حالا machine-enforced.)

---

## ۵. Anti-scope (چیزهایی که عمداً نمی‌سازیم)

| مورد | چرا |
|------|-----|
| Neo4j / Graph DB | assertion store ما triple-based است؛ اگر لازم شد export می‌کنیم. هزینه‌ی عملیاتی زودهنگام |
| Globe سه‌بعدی / Cesium | تجربه‌ی ما 2D/2.5D است؛ 3D تمرکز را از «کشف داده» به «جلوه‌ی بصری» می‌برد |
| Vector Tiles (فاز ۱) | تا وقتی feature count < ~50k، GeoJSON با field budget کافی است |
| AI chatbot | روی داده‌ای که هنوز کامل نیست، خروجی قابل اتکا نمی‌دهد؛ بعد از رسیدن پوشش داده |
| Stories / Compare / Discover | وابسته به داده‌ی غنی‌اند؛ بعد از Pilot |
| Real-time collaboration (CRDT) | تیم ۲–۵ نفره با workflow بازبینی کافی است |
| میکروسرویس | یک backend لایه‌بندی‌شده (modular monolith) تا وقتی scale ثابت نشده |

---

## ۶. اصلاحات پیشنهادی روی Roadmap

تغییرات نسبت به roadmap دست‌نوشته:

1. **Phase 0 — Walking Skeleton** (اضافه شد): repo + CI + یک مسیر عمودی نازک با fixture، تا معماری از هفته‌ی اول تست شود. ✅ انجام شد.
2. **Phase 1 — Foundation**: همان فهرست شما + قرارداد API + error model + logging + i18n scaffold. ✅ انجام شد.
3. **Phase 2 — Domain Model**: شامل assertion store، name_variant، place_geometry زمان‌دار، facetها. (schema نوشته شد؛ برای review)
4. **Phase 3 — Temporal & Calendar Engine**: موتور تقویم + precision + bitemporal + تست property-based. (هسته‌اش پیاده شد)
5. **Phase 3.5 — Data Ingestion & Lint** (جدید): fixture format، validatorها، import از Wikidata.
6. **Phase 4 — Spatial**: PostGIS، ایندکس‌ها، LOD، bbox/intersects/radius.
7. **Phase 5 — Historical API**: atlas/features, atlas/timeline, entities, articles, sources, search.
8. **Phase 6 — Editorial (زودتر از قبل)**: چون تیم پژوهشی دارید، ابزار ورود داده زودتر از polish بصری لازم است.
9. **Phase 7/8 — Map & Timeline** (می‌تواند موازی با ۵ جلو برود، چون Walking Skeleton داریم).
10. **Phase 9 — Semantic Zoom** به‌عنوان قرارداد داده (rank/min_zoom) نه فقط UI.
11. بقیه مطابق دست‌نوشته.

**Definition of Done برای هر فاز:** code + test + migration + doc + seed/fixture + این که یک کاربر واقعی بتواند آن را در UI ببیند.

---

## ۷. تصمیم‌هایی که همین الان گرفتیم (ارجاع به ADR)

| ADR | موضوع | وضعیت |
|-----|-------|-------|
| [0001](adr/0001-record-architecture-decisions.md) | ثبت تصمیم‌های معماری به‌صورت ADR | Accepted |
| [0002](adr/0002-modular-monolith-postgres-postgis.md) | Modular monolith + PostgreSQL/PostGIS | Accepted |
| [0003](adr/0003-assertion-centric-bitemporal-graph.md) | Assertion-centric, bitemporal knowledge graph | Accepted |
| [0004](adr/0004-place-with-kind-and-facets.md) | یک `Place` با `kind` + facet؛ War = Event | Accepted |
| [0005](adr/0005-calendar-and-temporal-representation.md) | موتور تقویم و بازنمایی زمانی | Accepted |
| [0006](adr/0006-multilingual-name-variants.md) | مدل نام چندزبانه/چندنوشتاری | Accepted |
| [0007](adr/0007-search-normalization.md) | نرمال‌سازی و جست‌وجوی فارسی/عربی | Accepted |
| [0008](adr/0008-ids-slugs-versions.md) | UUIDv7 + slug + نسخه‌ی قابل استناد | Accepted |
| [0009](adr/0009-api-contract.md) | قرارداد API v1 | Accepted |
| [0010](adr/0010-editorial-workflow-and-roles.md) | گردش‌کار ویرایش، نقش‌ها، audit | Accepted |
| [0011](adr/0011-map-delivery-geojson-then-pmtiles.md) | تحویل داده‌ی نقشه: GeoJSON → PMTiles | Accepted |
| [0012](adr/0012-region-aware-periodization.md) | دوره‌بندی منطقه‌آگاه و چندطرحی | Accepted |
| [0013](adr/0013-licensing-borders-policy.md) | لایسنس داده + سیاست نمایش مرزها | Accepted |
| [0014](adr/0014-dev-drivers-and-portability.md) | پورت‌پذیری: PostGIS در prod، fixture driver در dev بدون Docker | Accepted |

---

## ۸. سؤال‌های باز (نیاز به تصمیم شما)

این‌ها را نمی‌توان از کد استخراج کرد و باید پژوهشی/محصولی تصمیم گرفته شوند:

1. **طرح دوره‌بندی مرجع** چه باشد؟ (سیاسی-دودمانی یا باستان‌شناختی؟ و چه کسی صاحب آن است؟)
2. **سیاست transliteration**: EI3؟ یا «رایج‌ترین شکل انگلیسی»؟ (روی slug و نام انگلیسی اثر دارد)
3. **محدوده‌ی جغرافیایی پژوهشی** دقیقاً کجاست؟ «آذربایجان تاریخی» شامل قفقاز جنوبی (جمهوری آذربایجان امروزی) و کردستان/گیلان می‌شود یا نه؟ این تصمیم **سیاسی-پژوهشی** است و باید صریح و مستند باشد.
4. آیا **زنجان** و **تاریخ‌های oral/محلی** در scope هستند؟
5. لایسنس نهایی داده: **CC BY-SA 4.0** (پیشنهاد ما) یا CC BY-NC؟
6. آیا **تصویر/عکس** (عکس تاریخی، پلان بنا) در MVP هست یا بعداً؟ (نیاز به asset storage و حق نشر دارد)
