import Link from "next/link";
import { notFound } from "next/navigation";

import { LOCALES, statusLabel, t } from "@/lib/i18n";
import { serverArticles } from "@/lib/serverApi";
import type { Locale } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ArticlesPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale: rawLocale } = await params;
  if (!(LOCALES as string[]).includes(rawLocale)) notFound();
  const locale = rawLocale as Locale;
  const listing = await serverArticles(locale, 50);
  const articles = listing?.data ?? [];

  return (
    <main className="doc-page">
      <div className="doc-container">
        <nav className="row" style={{ marginBottom: 18 }}>
          <Link className="btn" href={`/${locale}`}>← {t(locale, "ui.nav.atlas")}</Link>
        </nav>
        <h1 className="doc-title">{t(locale, "ui.nav.articles")}</h1>
        <p className="doc-lede">
          {locale === "fa"
            ? "مقاله‌ها روایت پژوهشی‌اند؛ داده‌های مکانی و زمانی در موجودیت‌ها نگهداری می‌شود و مقاله فقط به آن‌ها پیوند می‌خورد."
            : "Articles are the narrative layer; spatial and temporal facts live in entities, and articles only link to them."}
        </p>
        <div className="card-grid">
          {articles.map((article) => (
            <Link className="card" key={article.id} href={`/${locale}/article/${article.slug ?? article.id}`}>
              <h4>{article.label}</h4>
              <p>{article.label_secondary}</p>
              <p style={{ marginTop: 6 }}>
                <span className="chip">{statusLabel(locale, article.status)}</span>{" "}
                {article.t_display ? <span className="chip">{article.t_display}</span> : null}
              </p>
            </Link>
          ))}
        </div>
      </div>
    </main>
  );
}
