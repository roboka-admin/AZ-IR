# نقشه، تایم‌لاین و UX فرانت‌اند

> مرجع: ADR-0011 (delivery/basemap), ADR-0013 (uncertainty visuals), قانون ۵ و ۱۷ AGENTS.md

## ۱. اصول

1. **MapLibre فقط renderer است.** هر تصمیمی درباره‌ی «چه چیزی در سال X وجود داشته» از API می‌آید.
2. **هر state در URL است**: `/?z=8&c=38.25,48.29&t=1450&mode=at&l=places,buildings&e=plc_…`
   → هر نما قابل اشتراک، قابل بوک‌مارک، و قابل ارجاع در مقاله.
3. **RTL-first**: `dir="rtl"` روی `<html>` برای `fa`، فونت Vazirmatn self-hosted،
   فقط logical properties در CSS (`margin-inline-start`).
4. **هیچ فکت تاریخی hard-code نیست**؛ حتی متن disclaimer و نام لایه‌ها از `/api/v1/meta` می‌آید.
5. **همیشه یک نمای غیرنقشه‌ای موازی وجود دارد** (List view) با همان query → a11y و low-bandwidth.

## ۲. ساختار صفحه‌ی اطلس

```
┌────────────────────────────────────────────────────────────────────────┐
│  header: لوگو · جست‌وجو · سوییچ زبان (fa/en) · «فهرست» · «درباره»      │
├───────────────┬────────────────────────────────────────────────────────┤
│  sidebar      │                                                        │
│  ─ لایه‌ها     │                    MAP (MapLibre)                      │
│  ─ فیلتر دوره │            basemap: modern | historical                │
│  ─ نتایج      │            disclaimer دائمی مرزها (پایین)              │
│  ─ entity card│                                                        │
├───────────────┴────────────────────────────────────────────────────────┤
│  TIMELINE: [ era bands ] [ century ticks ] [ year ticks ]  ▶ play      │
└────────────────────────────────────────────────────────────────────────┘
```

## ۳. Timeline با Temporal Zoom

سه سطح که با wheel/pinch روی خود تایم‌لاین زوم می‌شود:

| level | bucket | نمایش |
|-------|--------|-------|
| `era` | دوره‌های `period` | باستان · اسلامی · ایلخانی · تیموری/قره‌قویونلو · صفوی · قاجار · معاصر |
| `century` | ۵۰–۱۰۰ سال | 1200 1250 1300 … |
| `year` | ۱–۵ سال | 1490 1491 1492 … |

رفتار:
- کشیدن = scrub؛ **فیلتر زمانی local** روی featureهای موجود (هر feature `t_from/t_to` دارد) → ۶۰fps بدون network.
- رها کردن یا عبور از مرز bucket = refetch (debounce ۲۵۰ms).
- هیستوگرام چگالی از `/atlas/timeline` پشت تایم‌لاین رسم می‌شود (کاربر می‌بیند کجا داده داریم و کجا خالی است — **coverage honesty**).
- `▶ play`: حرکت از `from` به `to` با سرعت متناسب؛ در هر bucket یک کارت «اتفاق مهم» نشان می‌دهد.
- ورودی تاریخ با تقویم دیگر: سوییچ `میلادی / هجری قمری / هجری شمسی` که از طریق `cal=` به API می‌رود.

## ۴. Semantic zoom در UI

سطح از `meta.zoom_level` می‌آید (سرور تصمیم می‌گیرد)، ولی UI هم رفتار متناسب دارد:

| level | رفتار UI |
|-------|----------|
| `L0_region` | فقط label شهرهای بزرگ؛ hover = tooltip چگالی |
| `L1_area` | markerها + رویدادهای بزرگ؛ sidebar فهرست منطقه |
| `L2_city` | کلیک روی شهر → panel شهر با بناها/رویدادها/اشخاص |
| `L3_fabric` | بناها، محله‌ها، مسیرها؛ labelها فارسی با secondary لاتین |
| `L4_monument` | footprint + اجزای بنا + «تغییرات تاریخی» (mini-timeline همان بنا) |

## ۵. Entity card (پاسخ بخش ۱۱ دست‌نوشته)

```
┌──────────────────────────────────────┐
│ بقعهٔ شیخ صفی‌الدین اردبیلی           │
│Historical Complex · Safavid period   │
│ ۷۳۴–۱۰۳۹ ق · اطمینان: بالا            │
│──────────────────────────────────────│
│ ⚖ ۱ مورد اختلاف دربارهٔ سال آغاز      │  ← فقط وقتی has_disagreements
│──────────────────────────────────────│
│ مقالات مرتبط (۳)                      │
│  · تاریخچهٔ مجموعهٔ شیخ صفی            │
│  · معماری و تحول الحاقات               │
│  · اردبیل و صفویان                    │
│──────────────────────────────────────│
│ منابع (۵)  ·  روابط (۸)                │
│ [ کاوش ]  [ نمایش در نقشه ] [ دنبال کردن تاریخ ]│
└──────────────────────────────────────┘
```

- «کاوش» → صفحه‌ی entity با گراف روابط و `links.map`.
- «نمایش در نقشه» → `?e=…&z=…&t=…` (state در URL).
- «دنبال کردن تاریخ» (Phase 12) → `/atlas/context` + play خودکار timeline.

## ۶. زبان بصری عدم‌قطعیت (design tokens)

| وضعیت | token |
|-------|-------|
| قطعی | `--cert-exact` (خط توپر) |
| تقریبی | `--cert-approx` (نقطه‌چین) |
| بازسازی‌شده/نامطمئن | `--cert-uncertain` (هاشور + fill محو) |
| مورد اختلاف | `--disputed` (نشان ⚖ + رنگ هشدار ملایم) |
| منبع ضعیف | `--weak-source` (opacity ۰٫۵۵) |
| مرز اداری امروزی | `--modern-border` (خاکستری نازک، پیش‌فرض خاموش) |

## ۷. Basemap

- دو حالت: `modern` و `historical`.
  `historical`: نام‌های معاصر کم‌رنگ، مرزهای سیاسی محو، terrain برجسته‌تر، پالت کاغذِ کهنه (بدون افکت سنگین).
- فونت‌ها self-hosted (`Vazirmatn` برای fa، `Noto Sans` برای لاتین) → بدون Google Fonts.
- **fallback آفلاین**: اگر style/tile خارجی لود نشد، style داخلی حداقلی (پس‌زمینه + گرید + لایه‌های خودمان)
  فعال می‌شود و بنر «نقشه‌ی پایه در دسترس نیست» نمایش داده می‌شود. نقشه هرگز سفید نمی‌ماند.

## ۸. دسترسی‌پذیری (a11y)

- نقشه `role="application"` با توضیح متنی جایگزین و **نمای فهرستی موازی** از همان query.
- همه‌ی featureها با کیبورد قابل پیمایش (یک list مخفی از featureهای viewport؛ Enter = انتخاب).
- تایم‌لاین: `<input type="range">` معادل + `aria-valuetext` با نمایش انسانی تاریخ («حدود ۱۴۵۰ میلادی / ۸۵۴ قمری»).
- کنتراست AA؛ همه‌ی رنگ‌ها از tokenها (نه hard-code).
- `prefers-reduced-motion` → انیمیشن play و transitionها خاموش.

## ۹. ساختار frontend

```
frontend/src/
├── app/[locale]/            fa (default, rtl) · en
│   ├── layout.tsx           html lang/dir، فونت، توکن‌ها
│   ├── page.tsx             AtlasPage (map + timeline + sidebar)
│   ├── entity/[type]/[id]/  صفحه‌ی موجودیت
│   ├── article/[slug]/      مقاله + «View on Map»
│   └── list/                نمای فهرستی (a11y/low-bandwidth) — همان query
├── components/
│   ├── map/{AtlasMap,Layers,EntityPopup,Disclaimer}.tsx
│   ├── timeline/{Timeline,TemporalZoomRuler,Histogram,PlayButton}.tsx
│   ├── panel/{EntityCard,RelatedArticles,Disagreements,Sources}.tsx
│   └── ui/{…}
├── lib/{api.ts,atlasState.ts,format.ts,i18n.ts}
├── messages/{fa,en}.json
└── styles/{tokens.css,global.css}
```
`lib/api.ts` تنها جایی است که با `/api/v1` حرف می‌زند (typed, با abort و retry و ETag).
`next.config.mjs` یک `rewrites()` دارد: `/api/:path* → http://backend:8000/api/:path*`
→ مرورگر **هرگز** مستقیماً به localhost دیگری درخواست نمی‌دهد.
