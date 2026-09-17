import Link from "next/link";
import { notFound } from "next/navigation";

import OpenOnMap from "@/components/OpenOnMap";
import { LOCALES, certaintyLabel, formatYear, statusLabel, t } from "@/lib/i18n";
import { serverEntity } from "@/lib/serverApi";
import type { Locale, MapState } from "@/lib/types";

export const dynamic = "force-dynamic";

const TYPES = ["place", "person", "event", "political_entity", "article", "source", "period"];

/** A map view derived from the entity's own geometry and time span -- never hard-coded. */
function mapStateFor(detail: Awaited<ReturnType<typeof serverEntity>>): MapState | null {
  if (!detail) return null;
  const center = detail.geometry?.center ?? null;
  if (!center) return null;
  const year =
    detail.temporal?.year_from !== null && detail.temporal?.year_from !== undefined
      ? detail.temporal.year_from
      : undefined;
  return {
    center: [center[1], center[0]],
    zoom: Math.max(detail.min_zoom ?? 6, 8),
    time: year !== undefined ? { year } : {},
  };
}

export default async function EntityPage({
  params,
}: {
  params: Promise<{ locale: string; type: string; slug: string }>;
}) {
  const { locale: rawLocale, type, slug } = await params;
  if (!(LOCALES as string[]).includes(rawLocale)) notFound();
  if (!TYPES.includes(type)) notFound();
  const locale = rawLocale as Locale;
  const detail = await serverEntity(type, slug, locale);
  if (!detail) notFound();

  const mapState = mapStateFor(detail);
  const temporal = detail.temporal;

  return (
    <main className="doc-page">
      <div className="doc-container">
        <nav className="row" style={{ marginBottom: 18, gap: 8 }}>
          <Link className="btn" href={`/${locale}`}>← {t(locale, "ui.nav.atlas")}</Link>
          <span className="grow" />
          {mapState ? <OpenOnMap locale={locale} mapState={mapState} entityId={detail.id} /> : null}
        </nav>

        <h1 className="doc-title">{detail.names.display}</h1>
        <p className="doc-lede">
          {detail.names.display_secondary ? `${detail.names.display_secondary} · ` : ""}
          {detail.summary}
        </p>

        <div className="row-wrap" style={{ marginBottom: 20 }}>
          <span className="chip accent">{detail.kind_label ?? detail.kind ?? detail.entity_type}</span>
          {temporal?.display ? <span className="chip">{temporal.display}</span> : null}
          <span className="chip">{certaintyLabel(locale, detail.geometry?.certainty)}</span>
          <span className="chip">{statusLabel(locale, detail.status)}</span>
          {detail.disagreements.length > 0 ? (
            <span className="chip disputed">
              {t(locale, "ui.entity.disagreements")} · {detail.disagreements.length}
            </span>
          ) : null}
        </div>

        {detail.geometry?.note ? (
          <div className="callout" style={{ marginBottom: 18 }}>
            <strong>{t(locale, "ui.entity.certainty")}</strong>
            {detail.geometry.note}
          </div>
        ) : null}
        {detail.geometry?.kind === "uncertain_locus" ? (
          <div className="callout" style={{ marginBottom: 18 }}>
            <strong>{certaintyLabel(locale, detail.geometry?.certainty)}</strong>
            {t(locale, "ui.entity.derivedLocus")}
          </div>
        ) : null}

        <dl className="kv" style={{ marginBottom: 20 }}>
          <dt>{t(locale, "ui.entity.temporal")}</dt>
          <dd>
            {temporal
              ? `${
                  temporal.year_from !== null && temporal.year_to !== null
                    ? temporal.year_from === temporal.year_to
                      ? formatYear(temporal.year_from, locale)
                      : `${formatYear(temporal.year_from, locale)} — ${formatYear(temporal.year_to, locale)}`
                    : "—"
                } · ${temporal.precision} · ${temporal.confidence}`
              : "—"}
          </dd>
          <dt>id</dt>
          <dd style={{ fontFamily: "var(--font-mono)" }}>{detail.id}</dd>
          <dt>{t(locale, "ui.coverage.title")}</dt>
          <dd>
            {detail.counts.sources} {t(locale, "ui.entity.sources")} · {detail.counts.assertions} claims ·{" "}
            {detail.counts.articles} {t(locale, "ui.entity.articles")}
          </dd>
        </dl>

        {detail.names.alternatives.length > 0 ? (
          <section className="section">
            <h3>{locale === "fa" ? "شکل‌های دیگر نام" : "Name variants"}</h3>
            <div className="row-wrap">
              {detail.names.alternatives.map((name) => (
                <span className="chip" key={`${name.form}-${name.lang}`} title={name.note ?? undefined}>
                  {name.form} <span style={{ color: "var(--text-faint)" }}>{name.lang}</span>
                </span>
              ))}
            </div>
          </section>
        ) : null}

        {detail.disagreements.length > 0 ? (
          <section className="section">
            <h3>{t(locale, "ui.entity.disagreements")}</h3>
            <div className="callout disputed" style={{ marginBottom: 8 }}>{t(locale, "ui.entity.disputedWarning")}</div>
            {detail.disagreements.map((group) => (
              <div key={group.topic_fa} style={{ marginBottom: 12 }}>
                <strong>{group.topic}</strong>
                <ul className="prose" style={{ margin: "4px 0 0" }}>
                  {group.positions.map((position, index) => (
                    <li key={index}>
                      <span className="rel-value">{position.object_value ?? position.object_label}</span> — {position.note}
                      {position.t_display ? <span className="chip">{position.t_display}</span> : null}
                      {position.evidence?.length ? (
                        <span className="chip">{position.evidence.map((item) => item.source_id).join(", ")}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
        ) : null}

        <section className="section">
          <h3>{t(locale, "ui.entity.relationships")}</h3>
          {detail.relationships.length === 0 ? (
            <p className="source-meta">{t(locale, "ui.entity.noRelationships")}</p>
          ) : (
            detail.relationships.map((relation, index) => (
              <div className="rel" key={`${relation.predicate}-${relation.object_id ?? index}`}>
                <span className="rel-predicate">{relation.label}</span>
                {relation.object_id && relation.object_type ? (
                  <Link
                    className="rel-object"
                    href={`/${locale}/entity/${relation.object_type}/${relation.object_slug ?? relation.object_id}`}
                  >
                    {relation.object_label ?? relation.object_id}
                  </Link>
                ) : (
                  <span className="rel-value">{relation.object_value ?? "—"}</span>
                )}
                {relation.t_display ? <span className="chip">{relation.t_display}</span> : null}
                {relation.certainty ? <span className="chip">{certaintyLabel(locale, relation.certainty as never)}</span> : null}
              </div>
            ))
          )}
        </section>

        {detail.articles.length > 0 ? (
          <section className="section">
            <h3>{t(locale, "ui.entity.articles")}</h3>
            <div className="card-grid">
              {detail.articles.map((article) => (
                <Link className="card" key={article.id} href={`/${locale}/article/${article.slug ?? article.id}`}>
                  <h4>{article.label}</h4>
                  <p>{article.label_secondary}</p>
                </Link>
              ))}
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
                <a href={source.url} target="_blank" rel="noreferrer noopener" className="source-meta" style={{ color: "var(--teal-400)" }}>
                  {source.url}
                </a>
              ) : null}
            </div>
          ))}
        </section>

        {detail.coverage_note ? (
          <section className="section">
            <h3>{t(locale, "ui.entity.coverage")}</h3>
            <div className="callout">{detail.coverage_note}</div>
          </section>
        ) : null}
      </div>
    </main>
  );
}
