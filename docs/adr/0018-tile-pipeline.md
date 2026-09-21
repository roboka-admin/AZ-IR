# ADR-0018 — خط تولید tile: PMTiles دست‌ساز، تحویل دوگانه، و زمان سمت کلاینت

**Status:** Accepted · **Date:** 2026-09-14 · **Relates to:** ADR-0011, ADR-0009, ADR-0015, ADR-0017, docs/06, docs/08

## Context
ADR-0011 تصمیم را گرفته بود (GeoJSON امروز، PMTiles فردا)؛ این سند «فردا» را می‌سازد. سه مانع
واقعی سر راه بود:

1. **وابستگی.** کتابخانهٔ پایتونی که هم PMTiles v3 بنویسد هم MVT، یا وجود نداشت یا با خودش
   GDAL/tippecanoe می‌آورد — باینری خارجی که در CI و روی PaaS دردسر است و AGENTS.md هم می‌گوید
   «وابستگی غیرضروری اضافه نکن».
2. **زمان.** tile بی‌زمان یعنی نقشهٔ تاریخیِ غلط. اگر فیلتر زمان سمت سرور بماند، هر حرکتِ
   تایم‌لاین یک round-trip شبکه است (scrub غیرممکن). اگر سمت کلاینت برود، «منطق تاریخی در
   مرورگر» است که AGENTS.md ممنوع کرده. باید مرز را دقیق کشید.
3. **دو مسیر تحویل.** تا وقتی archive ساخته نشده، نقشه باید همان GeoJSON قبلی باشد — بدون دو
   تعریفِ لایه، بدون دو style، و بدون fork کردن فرانت‌اند.

## Decision

### ۱) فرمت را خودمان نوشتیم؛ اعتبار را به خوانندهٔ مرجع سپردیم
`backend/src/azir/tiles/` سه فایل است: `protobuf.py` (varint/zigzag/message)، `mvt.py`
(MVT v2: layer/feature/geometry، extent=4096)، `pmtiles.py` (header ۱۲۷ بایتی، directory با
delta + run-length، leaf directory وقتی root از ۱۶۲۵۷ بایت بزند، brotli رد می‌شود چون
MapLibre آن را نمی‌خواند). **هیچ وابستگی تازه‌ای اضافه نشد.**

در عوض، اعتبار از بیرون می‌آید و در CI سنجیده می‌شود:
* `scripts/verify-pmtiles.mjs` با `pmtiles@4.5.0` — همان کتابخانه‌ای که MapLibre در مرورگر
  استفاده می‌کند — archive را باز می‌کند، metadata و `vector_layers` را می‌خواند، چند tile را
  بیرون می‌کشد و ساختار MVTشان را چک می‌کند. هم از دیسک، هم **روی HTTP** با range request.
* ۹ بردارِ Hilbert در `tests/test_tiles.py` مستقیماً از `zxyToTileId` همان کتابخانه خوانده شده‌اند.
* tile id شمارهٔ **تجمعیِ منحنی Hilbert** است، نه row-major؛ جایی که یک writer دست‌ساز بی‌صدا
  غلط می‌کند و نتیجه نقشهٔ سفید است، نه exception.

قاعدهٔ کلی: encoder دست‌ساز فقط وقتی مجاز است که یک reader مرجعِ مستقل، خروجی‌اش را در CI تأیید
کند. بدون آن بررسی، این کد نباید merge می‌شد.

### ۲) archive = (زبان، نسخهٔ tileset، revision داده)؛ pointer = انتشار اتمیک
`atlas-{locale}-{tileset_version}-{data_revision}.pmtiles` کنار `latest.json` که **نگاشتِ زبان به
آرشیو** است (`{"default_locale":"fa","locales":{"fa":{…},"en":{…}}}`). `data_revision` =
blake2b (۱۶ رقم hex) روی پیکرهٔ منتشرشده، پس نام فایل وقتی عوض می‌شود که **محتوا** عوض شده باشد.
فایل‌ها immutable → `cache-control: public, max-age=31536000, immutable`؛ pointer کوتاه‌مدت cache
می‌شود. انتشار و rollback هر دو یعنی «pointer را عوض کن»، و build یک زبان، زبان دیگر را از pointer
حذف نمی‌کند.

زبان **در نام فایل** است چون در محتواست: `label` در زمان build از `NameVariant` همان locale انتخاب
می‌شود (ADR-0006). بدون آن، دو build با یک revision روی هم می‌ریختند و یک بازدیدکنندهٔ انگلیسی
برچسب فارسی می‌خواند — بی‌هیچ خطایی در هیچ لایه‌ای. پس `index.json?locale=en` یا آرشیوِ `en` را
می‌دهد یا هیچ؛ و «هیچ» یعنی fallback روی مسیر زنده، که درست است.

tileها از snapshot **published** ساخته می‌شوند: draft/in_review هرگز در tile عمومی نمی‌آید
(ADR-0017) و preview ویرایش از مسیر GeoJSON زنده است.

### ۳) زمان در attribute می‌ماند، نه در انتخابِ tile
یک entity می‌تواند چند **گونهٔ زمانی** داشته باشد (اردبیل با هندسهٔ صفوی و هندسهٔ معاصر). هر گونه
یک MVT feature جداست با `t_from`, `t_to`, `t_display`, `t_precision`, `certainty`, `rank`,
`status`، نامِ همان locale (`label`, `label_secondary`, `dir`) و — وقتی هندسهٔ عمومی با هندسهٔ
تاریخی فرق دارد — `g_from`/`g_to`/`g_index`.
هر feature فقط در بهترین LOD ممکن نوشته می‌شود (نه در همهٔ زوم‌ها)، ولی شکافِ LOD هرگز یک entity
را پنهان نمی‌کند (`test_an_lod_gap_never_hides_an_entity`).

فیلتر زمان سمت کلاینت است، اما **به‌صورت expression روی attribute**، نه منطق تاریخی:
`coalesce(g_from, t_from, -1e6)` و `coalesce(g_to, t_to, +1e6)`، در حالت `during` بازهٔ تایم‌لاین
باید داخل بازهٔ feature باشد، و feature بی‌تاریخ همیشه رد می‌شود (حذف نمی‌شود: نبودِ تاریخ یک
داده است، نه مجوزِ پنهان‌کردن). نتیجه: یک دانلود، هزار فریم؛ scrub بدون شبکه.

### ۴) تحویل دوگانه با یک style
`frontend/src/lib/mapStyle.ts` یک registry دارد: هر لایه یک paint spec و یک `sourceLayer` نام
است و id نهایی `spec@layer`. `ensureAtlasLayers(map, font, binding | null)` همان specها را روی
GeoJSON source یا روی vector source می‌نشاند و `setDelivery()` زنده جابه‌جا می‌کند — بدون
بازسازی نقشه. visibility در حالت vector با `layout.visibility` و در حالت GeoJSON با فیلتر
`["in","layer",…]` (چون آن‌جا یک source چند لایه را حمل می‌کند). ترتیب paint مهم است: لایه‌ها به
ترتیب **افزوده‌شدن** نقاشی می‌شوند، پس حلقهٔ بیرونی روی specهاست (fill قبل از label) و حلقهٔ
داخلی روی source-layerها.

### ۵) تقویم سمت سرور می‌ماند: `GET /api/v1/atlas/window`
tileها با سالِ میلادیِ نرمال‌شده فیلتر می‌شوند. مرورگر نباید julian→gregorian تبدیل کند (منطق
تاریخی در کلاینت = ممنوع). پس یک endpoint پنجرهٔ زمانی را نرمال می‌کند و frontend فقط **عدد**
می‌گیرد و در expression می‌گذارد. `test_the_window_endpoint_agrees_with_features` پنجره را با
`meta.time` همان features روی ۶ حالت مقایسه می‌کند؛ چون هر دو از `_build_window` می‌آیند، این تست
واگرایی را غیرممکن می‌کند نه فقط نامحتمل.

### ۶) سیاست پیش‌فرض: `src=auto` یعنی archive، **اگر ساخته شده باشد**
`GET /api/v1/tiles/index.json` می‌گوید چه چیزی موجود است: `mode` (`pmtiles` وقتی archive **برای
همین locale** هست، وگرنه `dynamic`)، `archive.url/bytes/tiles/revision/locale`، `archive_locales`،
`dynamic`، `layers` (به ترتیب paint) و نوع propertyها.
کلاینت سه حالت دارد و انتخاب در URL می‌ماند (ADR-0015):
* `auto` (پیش‌فرض): PMTiles **فقط** اگر archive ساخته شده باشد؛
* `tiles`: endpoint پویای `{z}/{x}/{y}.pbf`؛
* `geojson`: همان مسیر قبلی.

نکتهٔ سیاستی: **روشن بودنِ endpoint پویا به‌تنهایی پیش‌فرض را عوض نمی‌کند.** ساختن archive یک
تصمیم عملیاتی است (یک job، یک هزینه، یک لحظهٔ انتشار) و تجربهٔ پیش‌فرض نباید بی‌صدا تغییر کند.
و وقتی fallback رخ می‌دهد، پنل **دلیلش** را می‌گوید (آرشیو نیست / فقط برای زبان دیگر هست / رندر
پویا خاموش است): کلیدی که بی‌صدا کار نکند، باگی است که هیچ‌کس گزارش نمی‌کند.

### ۷) سه endpoint، بدون منطق در router
* `/api/v1/tiles/index.json` — pointer؛
* `/api/v1/tiles/{z}/{x}/{y}.pbf` — render پویا: gzip + `ETag`/`304`، tile خالی `200` با
  `x-azir-empty: 1` (چون «اینجا چیزی نیست» یک پاسخ معتبر است، نه ۴۰۴)، بیرون از pyramid = ۴۰۴
  problem+json، خاموش بودنِ قابلیت = ۵۰۳؛
* `/api/v1/tiles/archive/{filename}` — range/`206` + immutable.

همه از `services/tiles.py` می‌آیند؛ router فقط HTTP است (AGENTS.md). نام archive با فهرستِ
فایل‌های واقعیِ directory مقایسه می‌شود، پس `..%2F..%2F` هیچ معنایی ندارد.

## Consequences

**مثبت**
* پیکرهٔ اردبیل: **۱۰۱۰ tile در z0..z10، ۸۴۶٬۷۲۳ بایت، ۵۶۴۷ feature، ۲٫۵ ثانیه ساخت.** کل
  اطلس در یک فایل که روی هر object storage/CDN می‌نشیند؛ بدون tile server اختصاصی.
* dedupe + run-length: ۱۰۱۰ tile → ۶۶۹ entry → ۵۶۸ blob (tileهای یکسان در زوم‌های پایین).
* سقف ۵۱۲ کیلوبایتیِ GeoJSON (ADR-0009) دیگر محدودیتِ viewportهای شلوغ نیست.
* CI هم writer را می‌سنجد هم مسیر HTTP را: اگر پروکسی `Range` را بخورد، build قرمز می‌شود، نه
  نقشهٔ کاربر. `scripts/smoke.sh` هم ۱۰ بررسی tile دارد (۴۴ بررسی کل).

**منفی / هزینه**
* tileها تا rebuild عقب‌اند: بعد از هر انتشار باید `make tiles` اجرا شود (در production: job روی رویداد
  publish). دادهٔ تازه بدون rebuild دیده نمی‌شود.
* به‌ازای هر زبان یک آرشیو و یک build: n زبان = n برابر زمان ساخت و n برابر فضا (برای پیکرهٔ
  فعلی: ۲ × ~۸۵۰ کیلوبایت).
* یک encoder دست‌ساز یعنی یک سطح نگهداری بیشتر، و فقط تا وقتی قابل اعتماد است که بررسی‌های مرجع
  سبز بمانند.
* زومِ بیشتر از `AZIR_TILES_MAX_ZOOM` در archive نیست → یا build بزرگ‌تر، یا fallback روی render
  پویا. فعلاً render پویا پوشش می‌دهد و پیامِ «tile در این زوم نداریم» دروغ نمی‌گوید.
* archive همهٔ گونه‌های زمانی را حمل می‌کند، پس از یک snapshot تک‌زمانی بزرگ‌تر است — بهایِ scrub
  بدون شبکه.

**پنج چیز که این مسیر لو داد** (و حالا تست دارد)
1. `TimeWindow.representative_year` برای انتخاب هندسه کافی نبود: باید **همهٔ** هندسه‌های داخل
   پنجره باز می‌شدند، وگرنه یک دورهٔ کوتاه در زوم پایین ناپدید می‌شد.
2. winding در MVT برعکسِ GeoJSON است (محور y به سمت پایین): ringها باید shoelace **مثبت** داشته
   باشند، وگرنه fill به‌جای شکل، حفره می‌سازد.
3. MVT نه `null` می‌پذیرد نه آرایه → هر property باید string/number/bool شود.
4. `pmtiles` در JS از `getZxy` بایتِ **بازشده** (gunzip) برمی‌گرداند؛ اگر فرض کنیم خام است،
   بررسیِ مرجع بی‌صدا غلط می‌شود.
5. نامِ بدون locale + pointer تک‌خانه‌ای = `index.json?locale=en` همان آرشیوِ فارسی را می‌داد.
   `test_the_index_never_hands_out_another_locales_archive` و یک بررسیِ smoke همین را پین می‌کنند؛
   `make tiles` حالا هر دو زبان را می‌سازد.
