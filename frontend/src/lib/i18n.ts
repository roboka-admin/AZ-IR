/**
 * In-house i18n (no extra dependency, ADR-0006 / locked decision: fa + en from day one).
 *
 * Two kinds of strings live here and nowhere else:
 *   1. interface chrome (buttons, headings, hints) -- translated below;
 *   2. formatting rules for numbers and years, including the Persian BCE convention.
 *
 * Historical *content* is never translated here: it arrives from the API already localized, and
 * competing readings stay visible as disagreements rather than being resolved in the UI.
 */

import type { CalendarCode, Certainty, Locale, TemporalMode } from "./types";

export const LOCALES: Locale[] = ["fa", "en"];
export const DEFAULT_LOCALE: Locale = "fa";

export const DIRECTION: Record<Locale, "rtl" | "ltr"> = { fa: "rtl", en: "ltr" };

const MESSAGES = {
  fa: {
    "app.title": "اطلس تاریخی آذربایجان ایران",
    "app.tagline": "نقشه و زمان، دو محور اصلی کاوش در تاریخ یک منطقه",
    "app.shortTitle": "اطلس آذربایجان",

    "ui.loading": "در حال بارگذاری…",
    "ui.retry": "تلاش دوباره",
    "ui.close": "بستن",
    "ui.error.backend": "اتصال به سرویس اطلس برقرار نشد. مطمئن شوید بک‌اند در حال اجراست.",
    "ui.error.title": "خطا",

    "ui.search.placeholder": "جست‌وجوی مکان، شخص، رویداد یا مقاله…",
    "ui.search.title": "جست‌وجو",
    "ui.search.empty": "نتیجه‌ای یافت نشد.",
    "ui.search.hint": "نتایج بر پایهٔ تطبیق نام‌های فارسی، انگلیسی و دیگر شکل‌های نوشتاری است.",
    "ui.search.normalized": "شکل بهنجار: {query}",
    "ui.search.score": "امتیاز {score}",

    "ui.layers.title": "لایه‌ها",
    "ui.layers.empty": "این لایه هنوز داده‌ای ندارد.",
    "ui.layers.hint": "لایهٔ مرزهای امروزی جدا از دادهٔ تاریخی است و به‌طور پیش‌فرض خاموش است.",

    "ui.time.title": "زمان",
    "ui.time.play": "پخش",
    "ui.time.pause": "توقف",
    "ui.time.reset": "بازنشانی",
    "ui.time.calendar": "تقویم",
    "ui.time.mode": "حالت تطبیق",
    "ui.mode.at": "در این لحظه",
    "ui.mode.overlaps": "هم‌پوشانی",
    "ui.mode.during": "کاملاً در بازه",
    "ui.time.span": "بازهٔ زمانی",
    "ui.time.year": "سال",
    "ui.time.notable": "رویدادهای شاخص",
    "ui.time.bucket": "اندازهٔ پله: {bucket} سال",

    "ui.zoom.level": "سطح معنایی",
    "ui.zoom.hint": "در هر بزرگ‌نمایی فقط آنچه در آن مقیاس معنا دارد نشان داده می‌شود.",
    "ui.features.count": "{count} عارضه",
    "ui.features.truncated": "پاسخ به‌سبب سقف حجم کوتاه شد؛ برای دیدن همه، بزرگ‌نمایی کنید یا لایه‌ها را محدودتر کنید.",
    "ui.features.payload": "{kb} کیلوبایت",

    "ui.entity.summary": "خلاصه",
    "ui.entity.relationships": "روابط",
    "ui.entity.disagreements": "اختلاف نظر پژوهشی",
    "ui.entity.sources": "منابع",
    "ui.entity.articles": "مقالات",
    "ui.entity.temporal": "بازهٔ زمانی",
    "ui.entity.certainty": "دقت مکانی",
    "ui.entity.coverage": "یادداشت پوشش",
    "ui.entity.openArticle": "خواندن مقاله",
    "ui.entity.showOnMap": "نمایش روی نقشه",
    "ui.entity.related": "مرتبط",
    "ui.entity.noRelationships": "روابطی ثبت نشده است.",
    "ui.entity.disputedWarning":
      "این موضوع میان پژوهشگران محل اختلاف است. هر دو دیدگاه با منبع نگه داشته شده و هیچ‌کدام حذف نشده‌اند.",
    "ui.entity.otherGeometries": "هندسه‌های دیگر (زمان‌دار)",
    "ui.entity.needsDigitisation": "این شکل هنوز نیازمند رقومی‌سازی دقیق از منبع است.",
    "ui.entity.derivedLocus": "مکان از رابطه با یک مکان دیگر مشتق شده است، نه از یک هندسهٔ ثبت‌شده.",

    "ui.article.readTime": "{minutes} دقیقه مطالعه",
    "ui.article.entities": "موجودیت‌های مرتبط",
    "ui.article.published": "منتشرشده",
    "ui.article.inReview": "در حال بازبینی",
    "ui.article.draft": "پیش‌نویس",

    "ui.coverage.title": "پوشش داده",
    "ui.coverage.disclaimer": "مرزهای تاریخی بازسازی‌شده‌اند و با مرزهای اداری امروزی تفاوت دارند.",
    "ui.coverage.gaps": "خلأهای شناخته‌شده",
    "ui.coverage.disputed": "{count} ادعای محل اختلاف",
    "ui.coverage.provisional": "{count} هندسهٔ موقت",

    "ui.about.title": "دربارهٔ اطلس",
    "ui.nav.atlas": "اطلس",
    "ui.nav.articles": "مقالات",
    "ui.nav.sources": "منابع",
    "ui.nav.about": "درباره",

    "certainty.exact": "دقیق",
    "certainty.approximate": "تقریبی",
    "certainty.uncertain": "نامعین",
    "certainty.reconstructed": "بازسازی‌شده",

    "calendar.gregorian_proleptic": "میلادی",
    "calendar.julian": "جولیان",
    "calendar.islamic_lunar": "قمری",
    "calendar.persian_solar": "شمسی",
    "calendar.unknown": "نامعلوم",

    "era.bce": "ق.م",
    "era.ce": "م",
  },
  en: {
    "app.title": "Historical Atlas of Iranian Azerbaijan",
    "app.tagline": "Map and time as the two axes of exploring a region's history",
    "app.shortTitle": "AZ-IR Atlas",

    "ui.loading": "Loading…",
    "ui.retry": "Retry",
    "ui.close": "Close",
    "ui.error.backend": "Could not reach the atlas service. Make sure the backend is running.",
    "ui.error.title": "Error",

    "ui.search.placeholder": "Search places, people, events or articles…",
    "ui.search.title": "Search",
    "ui.search.empty": "No results.",
    "ui.search.hint": "Results match Persian, English and other spelling variants of a name.",
    "ui.search.normalized": "Normalized: {query}",
    "ui.search.score": "score {score}",

    "ui.layers.title": "Layers",
    "ui.layers.empty": "This layer has no data yet.",
    "ui.layers.hint": "Modern borders are a separate layer, off by default, and never mixed with history.",

    "ui.time.title": "Time",
    "ui.time.play": "Play",
    "ui.time.pause": "Pause",
    "ui.time.reset": "Reset",
    "ui.time.calendar": "Calendar",
    "ui.time.mode": "Matching mode",
    "ui.mode.at": "at this moment",
    "ui.mode.overlaps": "overlapping",
    "ui.mode.during": "fully within",
    "ui.time.span": "Time span",
    "ui.time.year": "Year",
    "ui.time.notable": "Notable events",
    "ui.time.bucket": "Bucket: {bucket} years",

    "ui.zoom.level": "Semantic level",
    "ui.zoom.hint": "Each zoom shows only what is meaningful at that scale.",
    "ui.features.count": "{count} features",
    "ui.features.truncated":
      "The response was trimmed to stay inside the payload budget; zoom in or narrow the layers to see more.",
    "ui.features.payload": "{kb} KB",

    "ui.entity.summary": "Summary",
    "ui.entity.relationships": "Relationships",
    "ui.entity.disagreements": "Scholarly disagreement",
    "ui.entity.sources": "Sources",
    "ui.entity.articles": "Articles",
    "ui.entity.temporal": "Time span",
    "ui.entity.certainty": "Spatial certainty",
    "ui.entity.coverage": "Coverage note",
    "ui.entity.openArticle": "Read the article",
    "ui.entity.showOnMap": "Show on map",
    "ui.entity.related": "Related",
    "ui.entity.noRelationships": "No relationships recorded.",
    "ui.entity.disputedWarning":
      "Scholars disagree on this point. Both readings are kept with their sources; neither is hidden.",
    "ui.entity.otherGeometries": "Other geometries (time-bound)",
    "ui.entity.needsDigitisation": "This shape still needs precise digitisation from a source.",
    "ui.entity.derivedLocus": "The position is derived from a relation to another place, not from recorded geometry.",

    "ui.article.readTime": "{minutes} min read",
    "ui.article.entities": "Linked entities",
    "ui.article.published": "Published",
    "ui.article.inReview": "In review",
    "ui.article.draft": "Draft",

    "ui.coverage.title": "Data coverage",
    "ui.coverage.disclaimer": "Historical boundaries are reconstructions and differ from modern administrative borders.",
    "ui.coverage.gaps": "Known gaps",
    "ui.coverage.disputed": "{count} disputed claims",
    "ui.coverage.provisional": "{count} provisional shapes",

    "ui.about.title": "About the atlas",
    "ui.nav.atlas": "Atlas",
    "ui.nav.articles": "Articles",
    "ui.nav.sources": "Sources",
    "ui.nav.about": "About",

    "certainty.exact": "exact",
    "certainty.approximate": "approximate",
    "certainty.uncertain": "uncertain",
    "certainty.reconstructed": "reconstructed",

    "calendar.gregorian_proleptic": "Gregorian",
    "calendar.julian": "Julian",
    "calendar.islamic_lunar": "Hijri",
    "calendar.persian_solar": "Solar Hijri",
    "calendar.unknown": "unknown",

    "era.bce": "BCE",
    "era.ce": "CE",
  },
} as const;

export type MessageKey = keyof (typeof MESSAGES)["fa"];

export function t(locale: Locale, key: MessageKey, vars?: Record<string, string | number>): string {
  const template: string = MESSAGES[locale][key] ?? MESSAGES.en[key] ?? String(key);
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (_match, name: string) => String(vars[name] ?? `{${name}}`));
}

/* ------------------------------------------------------------------ formatting */

const PERSIAN_DIGITS = ["۰", "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹"];

export function localizeDigits(value: string | number, locale: Locale): string {
  const text = String(value);
  if (locale !== "fa") return text;
  return text.replace(/[0-9]/g, (digit) => PERSIAN_DIGITS[Number(digit)] ?? digit);
}

/**
 * Astronomical year numbering is used internally (year 0 exists), so a negative year means BCE:
 * -550 is 551 BCE. The API returns display strings for entities; this is for the scrubber, where
 * we only have a number.
 */
export function formatYear(year: number, locale: Locale): string {
  if (year <= 0) {
    const bce = 1 - year;
    return locale === "fa" ? `${localizeDigits(bce, locale)} ${t(locale, "era.bce")}` : `${bce} ${t(locale, "era.bce")}`;
  }
  return locale === "fa" ? `${localizeDigits(year, locale)} ${t(locale, "era.ce")}` : String(year);
}

export function formatNumber(value: number, locale: Locale, digits = 0): string {
  return localizeDigits(value.toFixed(digits), locale);
}

export function certaintyLabel(locale: Locale, certainty: Certainty | null | undefined): string {
  if (!certainty) return "—";
  return t(locale, `certainty.${certainty}` as MessageKey);
}

export function calendarLabel(locale: Locale, calendar: CalendarCode): string {
  return t(locale, `calendar.${calendar}` as MessageKey);
}

export function modeLabel(locale: Locale, mode: TemporalMode): string {
  return t(locale, `ui.mode.${mode}` as MessageKey);
}

export function statusLabel(locale: Locale, status: string): string {
  if (status === "published") return t(locale, "ui.article.published");
  if (status === "in_review") return t(locale, "ui.article.inReview");
  return t(locale, "ui.article.draft");
}
