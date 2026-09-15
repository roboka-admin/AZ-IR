"use client";

/**
 * The entity drawer: what a reader learns when they click something on the map.
 *
 * It shows the claim *and* its provenance: temporal precision, spatial certainty, sources,
 * related entities, and -- when scholars disagree -- every position side by side
 * (AGENTS.md rule 20: disagreements are preserved, never averaged away).
 */

import Link from "next/link";

import MarkdownLite from "./MarkdownLite";
import { certaintyLabel, formatYear, statusLabel, t } from "@/lib/i18n";
import type { EntityDetail, Locale, Relationship } from "@/lib/types";

export interface EntityDrawerProps {
  detail: EntityDetail;
  locale: Locale;
  loading?: boolean;
  onClose: () => void;
  onOpenEntity: (entityType: string, id: string) => void;
  onFlyTo: (center: [number, number], zoom?: number) => void;
}

function hrefFor(entityType: string, slug: string | null, id: string, locale: Locale): string {
  const key = slug ?? id;
  return entityType === "article" ? `/${locale}/article/${key}` : `/${locale}/entity/${entityType}/${key}`;
}

function RelationshipRow({
  relation,
  locale,
  onOpenEntity,
}: {
  relation: Relationship;
  locale: Locale;
  onOpenEntity: (entityType: string, id: string) => void;
}) {
  const target = relation.object_id && relation.object_type
    ? { type: relation.object_type, id: relation.object_id }
    : null;
  return (
    <div className="rel">
      <span className="rel-predicate">{relation.label}</span>
      {target ? (
        <button
          type="button"
          className="rel-object"
          onClick={() => onOpenEntity(target.type, target.id)}
          title={relation.object_label ?? target.id}
        >
          {relation.object_label ?? target.id}
        </button>
      ) : (
        <span className="rel-value">{relation.object_value ?? "—"}</span>
      )}
      {relation.t_display ? <span className="chip">{relation.t_display}</span> : null}
      {relation.status === "disputed" ? <span className="chip disputed">{t(locale, "ui.entity.disagreements")}</span> : null}
    </div>
  );
}

export default function EntityDrawer({ detail, locale, loading, onClose, onOpenEntity, onFlyTo }: EntityDrawerProps) {
  const geometry = detail.geometry;
  const center = geometry?.center ?? null;
  const derived = geometry?.kind === "uncertain_locus";
  const article = detail.article;
  const body = article
    ? locale === "fa"
      ? article.body_fa_md ?? article.body_md
      : article.body_en_md ?? article.body_md
    : detail.body_md;

  const firstArticle = detail.articles[0] ?? null;
  const entityLinks = (article?.entities ?? []).map((entity) => ({
    label: entity.label,
    href: hrefFor(entity.entity_type, entity.slug, entity.id, locale),
  }));

  return (
    <aside className="panel drawer" aria-label={detail.names.display} aria-busy={loading ?? false}>
      <header className="panel-title">
        <span className="row" style={{ gap: 6, textTransform: "none", fontSize: 13, color: "var(--text)" }}>
          <strong>{detail.names.display}</strong>
          {detail.names.display_secondary ? (
            <span style={{ color: "var(--text-faint)", fontWeight: 400 }}>{detail.names.display_secondary}</span>
          ) : null}
        </span>
        <button type="button" className="btn btn-icon btn-ghost" onClick={onClose} aria-label={t(locale, "ui.close")}>
          ✕
        </button>
      </header>

      <div className="scroller grow">
        <div className="row-wrap">
          <span className="chip accent">{detail.kind_label ?? detail.kind ?? detail.entity_type}</span>
          {detail.temporal?.display ? <span className="chip">{detail.temporal.display}</span> : null}
          {detail.status !== "published" ? (
            <span className="chip warn">{statusLabel(locale, detail.status)}</span>
          ) : null}
          {detail.disagreements.length > 0 ? (
            <span className="chip disputed">
              {t(locale, "ui.entity.disagreements")} · {detail.disagreements.length}
            </span>
          ) : null}
          {geometry?.needs_digitisation ? <span className="chip warn">⚠ {t(locale, "ui.entity.needsDigitisation")}</span> : null}
        </div>

        <div className="row" style={{ marginTop: 10, gap: 6 }}>
          {center ? (
            <button type="button" className="btn" onClick={() => onFlyTo([center[1], center[0]], Math.max(detail.min_zoom, 10))}>
              {t(locale, "ui.entity.showOnMap")}
            </button>
          ) : null}
          {firstArticle && !article ? (
            <Link className="btn btn-primary" href={hrefFor("article", firstArticle.slug, firstArticle.id, locale)}>
              {t(locale, "ui.entity.openArticle")}
            </Link>
          ) : null}
          {article ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => onFlyTo([article.map_state.center[0], article.map_state.center[1]], article.map_state.zoom)}
            >
              {t(locale, "ui.entity.showOnMap")}
            </button>
          ) : null}
        </div>

        {derived ? (
          <div className="callout" style={{ marginTop: 10 }}>
            <strong>{certaintyLabel(locale, geometry?.certainty)}</strong>
            {t(locale, "ui.entity.derivedLocus")}
            {geometry?.note ? <div style={{ marginTop: 4 }}>{geometry.note}</div> : null}
          </div>
        ) : geometry?.note ? (
          <div className="callout" style={{ marginTop: 10 }}>
            <strong>
              {t(locale, "ui.entity.certainty")}: {certaintyLabel(locale, geometry.certainty)}
            </strong>
            {geometry.note}
          </div>
        ) : null}

        <dl className="kv section">
          <dt>{t(locale, "ui.entity.temporal")}</dt>
          <dd>
            {detail.temporal
              ? `${
                  detail.temporal.year_from !== null && detail.temporal.year_to !== null
                    ? detail.temporal.year_from === detail.temporal.year_to
                      ? formatYear(detail.temporal.year_from, locale)
                      : `${formatYear(detail.temporal.year_from, locale)} — ${formatYear(detail.temporal.year_to, locale)}`
                    : "—"
                } · ${detail.temporal.precision} · ${detail.temporal.confidence}`
              : "—"}
          </dd>
          <dt>{t(locale, "ui.entity.certainty")}</dt>
          <dd>{certaintyLabel(locale, geometry?.certainty)}</dd>
          {detail.attestation ? (
            <>
              <dt>attestation</dt>
              <dd>{detail.attestation}</dd>
            </>
          ) : null}
          <dt>{t(locale, "ui.coverage.title")}</dt>
          <dd>
            {detail.counts.sources} {t(locale, "ui.entity.sources")} · {detail.counts.assertions} claims ·{" "}
            {detail.counts.articles} {t(locale, "ui.entity.articles")}
          </dd>
        </dl>

        {detail.summary ? (
          <section className="section">
            <h3>{t(locale, "ui.entity.summary")}</h3>
            <p className="prose" style={{ margin: 0 }}>
              {detail.summary}
            </p>
          </section>
        ) : null}

        {detail.names.alternatives.length > 0 ? (
          <section className="section">
            <h3>{locale === "fa" ? "شکل‌های دیگر نام" : "Name variants"}</h3>
            <div className="row-wrap">
              {detail.names.alternatives.map((name) => (
                <span key={`${name.form}-${name.lang}`} className="chip" title={name.note ?? undefined}>
                  {name.form}
                  <span style={{ color: "var(--text-faint)" }}>
                    {name.lang}
                    {name.year_from || name.year_to
                      ? ` · ${name.year_from ?? ""}–${name.year_to ?? ""}`
                      : ""}
                  </span>
                </span>
              ))}
            </div>
          </section>
        ) : null}

        {geometry && geometry.other_geometries.length > 0 ? (
          <section className="section">
            <h3>{t(locale, "ui.entity.otherGeometries")}</h3>
            {geometry.other_geometries.map((other, index) => (
              <div className="source" key={`${other.kind}-${index}`}>
                <div className="source-title">
                  {other.kind} · {certaintyLabel(locale, other.certainty)}
                  {other.year_from !== null && other.year_to !== null
                    ? ` · ${formatYear(other.year_from, locale)}–${formatYear(other.year_to, locale)}`
                    : ""}
                </div>
                {other.note ? <div className="source-meta">{other.note}</div> : null}
              </div>
            ))}
          </section>
        ) : null}

        {detail.disagreements.length > 0 ? (
          <section className="section">
            <h3>{t(locale, "ui.entity.disagreements")}</h3>
            {detail.disagreements.map((group) => (
              <div className="callout disputed" key={group.topic_fa || group.topic_en} style={{ marginBottom: 8 }}>
                <strong>{group.topic}</strong>
                <div style={{ opacity: 0.85, marginBottom: 6 }}>{t(locale, "ui.entity.disputedWarning")}</div>
                {group.positions.map((position, index) => (
                  <div key={`${position.predicate}-${index}`} style={{ marginBottom: 6 }}>
                    <div className="row" style={{ gap: 6 }}>
                      <span className="chip">{position.label}</span>
                      <span className="rel-value">{position.object_value ?? position.object_label ?? ""}</span>
                      <span className="chip">{position.confidence}</span>
                    </div>
                    {position.note ? <div style={{ marginTop: 3 }}>{position.note}</div> : null}
                    {position.t_display ? <div className="source-meta">{position.t_display}</div> : null}
                    {position.evidence && position.evidence.length > 0 ? (
                      <div className="source-meta">
                        {position.evidence.map((item) => item.source_id).join("، ")}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            ))}
          </section>
        ) : null}

        {body ? (
          <section className="section">
            <h3>{article ? article.title : t(locale, "ui.entity.summary")}</h3>
            {article ? (
              <div className="row-wrap" style={{ marginBottom: 8 }}>
                {article.author ? <span className="chip">{article.author}</span> : null}
                {article.published_at ? <span className="chip">{article.published_at}</span> : null}
                {article.reading_time_min ? (
                  <span className="chip">{t(locale, "ui.article.readTime", { minutes: article.reading_time_min })}</span>
                ) : null}
              </div>
            ) : null}
            <MarkdownLite body={body} links={entityLinks} locale={locale} />
          </section>
        ) : null}

        <section className="section">
          <h3>{t(locale, "ui.entity.relationships")}</h3>
          {detail.relationships.length === 0 ? (
            <p className="source-meta">{t(locale, "ui.entity.noRelationships")}</p>
          ) : (
            detail.relationships.slice(0, 40).map((relation, index) => (
              <RelationshipRow key={`${relation.predicate}-${relation.object_id ?? index}`} relation={relation} locale={locale} onOpenEntity={onOpenEntity} />
            ))
          )}
        </section>

        {detail.articles.length > 0 && !article ? (
          <section className="section">
            <h3>{t(locale, "ui.entity.articles")}</h3>
            {detail.articles.map((item) => (
              <Link
                key={item.id}
                className="hit"
                href={hrefFor("article", item.slug, item.id, locale)}
                style={{ paddingInline: 0 }}
              >
                <span className="hit-title">{item.label}</span>
                <span className="hit-sub">
                  {item.label_secondary}
                  {item.t_display ? <span className="chip">{item.t_display}</span> : null}
                </span>
              </Link>
            ))}
          </section>
        ) : null}

        <section className="section">
          <h3>{t(locale, "ui.entity.sources")}</h3>
          {detail.sources.length === 0 ? (
            <p className="source-meta">—</p>
          ) : (
            detail.sources.map((source) => (
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
                {source.needs_review ? <span className="chip warn">needs review</span> : null}
              </div>
            ))
          )}
        </section>

        {detail.coverage_note ? (
          <section className="section">
            <h3>{t(locale, "ui.entity.coverage")}</h3>
            <div className="callout">{detail.coverage_note}</div>
          </section>
        ) : null}
      </div>
    </aside>
  );
}
