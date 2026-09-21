import Link from "next/link";
import { notFound } from "next/navigation";

import { LOCALES, localizeDigits, t } from "@/lib/i18n";
import { serverSources } from "@/lib/serverApi";
import type { Locale } from "@/lib/types";

export const dynamic = "force-dynamic";

const RELIABILITY_ORDER: Record<string, number> = { primary: 0, secondary: 1, tertiary: 2 };

export default async function SourcesPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale: rawLocale } = await params;
  if (!(LOCALES as string[]).includes(rawLocale)) notFound();
  const locale = rawLocale as Locale;
  const listing = await serverSources(locale, 200);
  const sources = [...(listing?.data ?? [])].sort(
    (left, right) =>
      (RELIABILITY_ORDER[left.reliability ?? "tertiary"] ?? 3) - (RELIABILITY_ORDER[right.reliability ?? "tertiary"] ?? 3),
  );

  return (
    <main className="doc-page">
      <div className="doc-container">
        <nav className="row" style={{ marginBottom: 18 }}>
          <Link className="btn" href={`/${locale}`}>← {t(locale, "ui.nav.atlas")}</Link>
        </nav>
        <h1 className="doc-title">{t(locale, "ui.nav.sources")}</h1>
        <p className="doc-lede">
          {locale === "fa"
            ? `هر ادعا در اطلس به دست‌کم یک منبع متصل است. ${localizeDigits(sources.length, locale)} منبع ثبت شده است؛ تنها اطلاعات کتاب‌شناختی و گزیده‌های کوتاه نگه داشته می‌شود، نه متن کامل دارای حق نشر.`
            : `Every claim in the atlas links to at least one source. ${sources.length} sources are registered; only bibliographic metadata and short quotations are stored, never copyrighted full text.`}
        </p>
        {sources.map((source) => (
          <div className="source" key={source.id}>
            <div className="source-title">
              {source.title} <span className="chip">{source.reliability ?? "—"}</span>
              {source.needs_review ? <span className="chip warn">needs review</span> : null}
            </div>
            <div className="source-meta">
              {[source.author, source.year, source.publisher, source.kind].filter(Boolean).join(" · ")}
            </div>
            {source.citation ? <div className="source-meta">{source.citation}</div> : null}
            {source.url ? (
              <a href={source.url} target="_blank" rel="noreferrer noopener" className="source-meta" style={{ color: "var(--teal-400)" }}>
                {source.url}
              </a>
            ) : null}
          </div>
        ))}
      </div>
    </main>
  );
}
