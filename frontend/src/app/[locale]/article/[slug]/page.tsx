import Link from "next/link";
import { notFound } from "next/navigation";

import MarkdownLite from "@/components/MarkdownLite";
import OpenOnMap from "@/components/OpenOnMap";
import { certaintyLabel, formatYear, statusLabel, t } from "@/lib/i18n";
import { serverArticle } from "@/lib/serverApi";
import { LOCALES } from "@/lib/i18n";
import type { Locale } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ArticlePage({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  const { locale: rawLocale, slug } = await params;
  if (!(LOCALES as string[]).includes(rawLocale)) notFound();
  const locale = rawLocale as Locale;
  const detail = await serverArticle(slug, locale);
  if (!detail) notFound();

  const article = detail.article;
  if (!article) notFound();

  const body = locale === "fa" ? article.body_fa_md ?? article.body_md : article.body_en_md ?? article.body_md;
  const links = article.entities.map((entity) => ({
    label: entity.label,
    href: `/${locale}/entity/${entity.entity_type}/${entity.slug ?? entity.id}`,
  }));

  return (
    <main className="doc-page">
      <div className="doc-container">
        <nav className="row" style={{ marginBottom: 18, gap: 8 }}>
          <Link className="btn" href={`/${locale}`}>← {t(locale, "ui.nav.atlas")}</Link>
          <Link className="btn" href={`/${locale}/articles`}>{t(locale, "ui.nav.articles")}</Link>
          <span className="grow" />
          <OpenOnMap locale={locale} mapState={article.map_state} entityId={detail.id} />
        </nav>

        <h1 className="doc-title">{article.title}</h1>
        <p className="doc-lede">
          {article.title_secondary && article.title_secondary !== article.title ? `${article.title_secondary} · ` : ""}
          {detail.summary}
        </p>

        <div className="row-wrap" style={{ marginBottom: 22 }}>
          {article.author ? <span className="chip accent">{article.author}</span> : null}
          {article.published_at ? <span className="chip">{article.published_at}</span> : null}
          {article.reading_time_min ? (
            <span className="chip">{t(locale, "ui.article.readTime", { minutes: article.reading_time_min })}</span>
          ) : null}
          <span className="chip">{statusLabel(locale, detail.status)}</span>
          {detail.disagreements.length > 0 ? (
            <span className="chip disputed">
              {t(locale, "ui.entity.disagreements")} · {detail.disagreements.length}
            </span>
          ) : null}
        </div>

        <MarkdownLite body={body} links={links} locale={locale} />

        <section className="section">
          <h3>{t(locale, "ui.article.entities")}</h3>
          <div className="card-grid">
            {article.entities.map((entity) => (
              <Link className="card" key={entity.id} href={`/${locale}/entity/${entity.entity_type}/${entity.slug ?? entity.id}`}>
                <h4>{entity.label}</h4>
                <p>
                  {entity.label_secondary ? `${entity.label_secondary} · ` : ""}
                  {entity.t_display ?? entity.kind_label ?? entity.entity_type}
                  {entity.relation ? ` · ${entity.relation}` : ""}
                </p>
              </Link>
            ))}
          </div>
        </section>

        {detail.disagreements.length > 0 ? (
          <section className="section">
            <h3>{t(locale, "ui.entity.disagreements")}</h3>
            <div className="callout disputed">{t(locale, "ui.entity.disputedWarning")}</div>
            {detail.disagreements.map((group) => (
              <div key={group.topic_fa} style={{ marginTop: 10 }}>
                <strong>{group.topic}</strong>
                <ul style={{ margin: "4px 0 0", paddingInlineStart: 20, fontSize: 13, lineHeight: 1.8 }}>
                  {group.positions.map((position, index) => (
                    <li key={index}>
                      {position.object_value ?? position.object_label} — {position.note ?? ""}{" "}
                      <span className="chip">{position.confidence}</span>
                      {position.t_display ? <span className="chip">{position.t_display}</span> : null}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
        ) : null}

        {detail.geometry?.note ? (
          <section className="section">
            <h3>{t(locale, "ui.entity.certainty")}</h3>
            <div className="callout">
              <strong>{certaintyLabel(locale, detail.geometry.certainty)}</strong>
              {detail.geometry.note}
              {detail.temporal?.year_from !== null && detail.temporal?.year_from !== undefined
                ? ` · ${formatYear(detail.temporal.year_from, locale)}–${
                    detail.temporal?.year_to !== null && detail.temporal?.year_to !== undefined
                      ? formatYear(detail.temporal.year_to, locale)
                      : ""
                  }`
                : ""}
            </div>
          </section>
        ) : null}

        <section className="section">
          <h3>{t(locale, "ui.entity.sources")}</h3>
          {detail.sources.map((source) => (
            <div className="source" key={source.id}>
              <div className="source-title">{source.title}</div>
              <div className="source-meta">
                {[source.author, source.year, source.publisher, source.reliability].filter(Boolean).join(" · ")}
              </div>
              {source.citation ? <div className="source-meta">{source.citation}</div> : null}
              {source.url ? (
                <a className="source-meta" href={source.url} target="_blank" rel="noreferrer noopener" style={{ color: "var(--teal-400)" }}>
                  {source.url}
                </a>
              ) : null}
            </div>
          ))}
        </section>
      </div>
    </main>
  );
}
