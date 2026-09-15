import Link from "next/link";

import { LOCALES, t } from "@/lib/i18n";
import { serverMeta } from "@/lib/serverApi";
import type { Locale } from "@/lib/types";

export const dynamic = "force-dynamic";

const COPY = {
  fa: {
    lede:
      "اطلس تاریخی تعاملی آذربایجان ایران یک زیرساخت پژوهشی است: پایگاه‌دادهٔ مکانی-زمانی، گراف دانش و دانشنامهٔ دیجیتال که نقشه و زمان دو محور اصلی کاوش در آن‌اند.",
    principles: [
      {
        title: "زمان، محور نخست",
        body: "هر عارضه بازهٔ زمانی و دقت زمانی دارد (سال دقیق، حدود، سده، بازه، پیش از/پس از). هیچ تاریخ تقریبی به‌شکل دقیق نمایش داده نمی‌شود.",
      },
      {
        title: "ادعا با منبع",
        body: "هر گزارهٔ تاریخی به دست‌کم یک منبع و در صورت امکان به شاهد (صفحه، نقل‌قول) متصل است. گزارهٔ بی‌منبع منتشر نمی‌شود.",
      },
      {
        title: "اختلاف نظر نگه داشته می‌شود",
        body: "وقتی پژوهشگران اختلاف دارند، هر دو دیدگاه با منبع و اعتمادبه‌نفس خودش نمایش داده می‌شود؛ اطلس آن را میانگین نمی‌گیرد یا پنهان نمی‌کند.",
      },
      {
        title: "مرز تاریخی ≠ مرز امروزی",
        body: "تقسیمات اداری امروزی لایه‌ای جدا است، به‌طور پیش‌فرض خاموش، و هرگز به‌عنوان مرز تاریخی نشان داده نمی‌شود.",
      },
      {
        title: "بزرگ‌نمایی معنایی",
        body: "در هر سطح بزرگ‌نمایی فقط آنچه در آن مقیاس معنا دارد نشان داده می‌شود: از کل منطقه تا بافت شهری و بنا.",
      },
      {
        title: "صداقت دربارهٔ خلأها",
        body: "خلأهای پوشش (رویداد بی‌مکان، هندسهٔ موقت، دادهٔ نیازمند رقومی‌سازی) به‌جای پنهان شدن گزارش می‌شوند.",
      },
    ],
    statusTitle: "وضعیت داده در این نمونه",
    statusBody:
      "دادهٔ نمایشیِ این نسخه بر پایهٔ منابع منتشرشده (تاریخ کمبریج ایران، دانشنامهٔ ایرانیکا، فهرست میراث جهانی، منابع دوره‌ای) ساخته شده و هندسه‌های سطحی آن بازسازی طرح‌واره‌ای‌اند؛ همه با certainty مناسب و یادداشت توضیحی علامت‌گذاری شده‌اند.",
    shortcuts: "میان‌برها: کلیدهای چپ و راست برای جابه‌جایی سال (با Shift به اندازهٔ ۲۵ سال)، Space برای پخش/توقف، Esc برای بستن صفحهٔ کناری.",
  },
  en: {
    lede: "The Interactive Historical Atlas of Iranian Azerbaijan is research infrastructure: a spatio-temporal database, a knowledge graph and a digital encyclopedia where map and time are the two axes of exploration.",
    principles: [
      { title: "Time is a first-class axis", body: "Every feature carries a time span and a precision (exact year, circa, century, range, before/after). An approximate date is never rendered as an exact one." },
      { title: "Claims carry sources", body: "Every historical statement links to at least one source and, where possible, to evidence (page, quotation). Unsupported claims are not published." },
      { title: "Disagreement is preserved", body: "Where scholars disagree, every position is shown with its own source and confidence; the atlas neither averages nor hides them." },
      { title: "Historical ≠ modern borders", body: "Present-day administration is a separate layer, off by default, and never presented as a historical boundary." },
      { title: "Semantic zoom", body: "Each zoom level shows only what is meaningful at that scale: from the whole region down to urban fabric and individual monuments." },
      { title: "Gaps are reported", body: "Coverage gaps (events without a location, provisional shapes, data awaiting digitisation) are surfaced instead of hidden." },
    ],
    statusTitle: "Data status in this build",
    statusBody: "The demonstration corpus is built from published scholarship (Cambridge History of Iran, Encyclopaedia Iranica, World Heritage dossiers, period sources). All area geometries are schematic reconstructions, flagged with the appropriate certainty and an explanatory note.",
    shortcuts: "Shortcuts: left/right arrows move the year (Shift for 25 years), Space toggles playback, Esc closes the side panel.",
  },
} as const;

export default async function AboutPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale: rawLocale } = await params;
  const locale: Locale = (LOCALES as string[]).includes(rawLocale) ? (rawLocale as Locale) : "fa";
  const meta = await serverMeta(locale);
  const copy = COPY[locale];

  return (
    <main className="doc-page">
      <div className="doc-container">
        <nav className="row" style={{ marginBottom: 18 }}>
          <Link className="btn" href={`/${locale}`}>← {t(locale, "ui.nav.atlas")}</Link>
        </nav>
        <h1 className="doc-title">{t(locale, "ui.about.title")}</h1>
        <p className="doc-lede">{copy.lede}</p>

        {meta ? (
          <div className="row-wrap" style={{ marginBottom: 22 }}>
            <span className="chip accent">{meta.study_area.name}</span>
            {Object.entries(meta.coverage)
              .filter(([, value]) => typeof value === "number")
              .map(([key, value]) => (
                <span className="chip" key={key}>
                  {key.replace(/_/g, " ")}: <strong style={{ color: "var(--text)" }}>{String(value)}</strong>
                </span>
              ))}
            <span className="chip">{meta.license.data}</span>
          </div>
        ) : null}

        <section className="section">
          <h3>{locale === "fa" ? "اصول" : "Principles"}</h3>
          <div className="card-grid">
            {copy.principles.map((principle) => (
              <div className="card" key={principle.title}>
                <h4>{principle.title}</h4>
                <p>{principle.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="section">
          <h3>{copy.statusTitle}</h3>
          <p className="prose">{copy.statusBody}</p>
          <div className="callout">{meta?.disclaimer.borders}</div>
          <p className="prose" style={{ marginTop: 12, color: "var(--text-dim)" }}>
            {copy.shortcuts}
          </p>
        </section>
      </div>
    </main>
  );
}
