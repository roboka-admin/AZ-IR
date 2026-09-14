"use client";

/**
 * The side panels: layers, search, legend, coverage and the status bar.
 *
 * Layer ids, labels, counts and defaults all come from `/api/v1/meta`; the components only render
 * and dispatch (AGENTS.md rules 2 and 19).
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { colorForLayer } from "@/lib/mapStyle";
import { certaintyLabel, formatNumber, localizeDigits, t } from "@/lib/i18n";
import type {
  CoverageStats,
  FeatureProperties,
  LayerInfo,
  Locale,
  TilesDelivery,
  TilesPreference,
  MetaResponse,
  SearchHit,
  ZoomLevelInfo,
} from "@/lib/types";

/* ------------------------------------------------------------------ layers */

export interface LayersPanelProps {
  locale: Locale;
  layers: LayerInfo[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onOnly: (id: string) => void;
  /** Tile delivery is a reader-facing choice, so it lives with the other map controls. */
  source: TilesPreference;
  delivery: TilesDelivery;
  /** Which locales have a built archive — the reason a fallback happened, when one did. */
  archiveLocales: string[];
  onSourceChange: (source: TilesPreference) => void;
}

/**
 * Explain a fallback instead of leaving the switcher to look broken.
 *
 * Asking for tiles and silently getting GeoJSON is the kind of failure nobody reports: the map still
 * works, it is just slower. So the hint names the cause — nothing built, built for another language,
 * or the render endpoint switched off on the server.
 */
function sourceHint(
  locale: Locale,
  source: TilesPreference,
  delivery: TilesDelivery,
  archiveLocales: string[],
): string {
  if (source === "geojson" || delivery !== "geojson") return t(locale, "ui.source.hint");
  if (source === "tiles") return t(locale, "ui.source.dynamic_off");
  return archiveLocales.length > 0
    ? t(locale, "ui.source.unavailable_locale", { locales: archiveLocales.join(" / ") })
    : t(locale, "ui.source.unavailable");
}

const SOURCE_CHOICES: TilesPreference[] = ["auto", "tiles", "geojson"];

export function LayersPanel({
  locale,
  layers,
  selected,
  onToggle,
  onOnly,
  source,
  delivery,
  archiveLocales,
  onSourceChange,
}: LayersPanelProps) {
  return (
    <section className="panel">
      <header className="panel-title">
        <span>{t(locale, "ui.layers.title")}</span>
      </header>
      <div className="layer-row" style={{ gap: 6, padding: "2px 0 8px" }}>
        <span style={{ fontSize: 11, color: "var(--text-dim)" }}>{t(locale, "ui.source.title")}</span>
        <div style={{ display: "flex", gap: 4, marginInlineStart: "auto" }}>
          {SOURCE_CHOICES.map((choice) => (
            <button
              key={choice}
              type="button"
              className="btn btn-compact"
              aria-pressed={source === choice}
              onClick={() => onSourceChange(choice)}
              title={t(locale, `ui.source.${choice}` as never)}
            >
              {t(locale, `ui.source.${choice}` as never)}
            </button>
          ))}
        </div>
      </div>
      <div className="legend" style={{ paddingTop: 0 }}>
        {sourceHint(locale, source, delivery, archiveLocales)}
      </div>
      <div style={{ padding: "4px 0 6px" }}>
        {layers.map((layer) => (
          <label className="layer-row" key={layer.id}>
            <input type="checkbox" checked={selected.has(layer.id)} onChange={() => onToggle(layer.id)} />
            <span className="dot" style={{ background: colorForLayer(layer.id) }} />
            <button
              type="button"
              className="btn-ghost grow"
              style={{ textAlign: "start", padding: 0, border: 0, background: "transparent", cursor: "pointer" }}
              onClick={() => onOnly(layer.id)}
              title={layer.id}
            >
              {layer.label}
            </button>
            <span className="layer-count">{localizeDigits(layer.count, locale)}</span>
          </label>
        ))}
      </div>
      <div className="legend" style={{ paddingTop: 0 }}>
        {t(locale, "ui.layers.hint")}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ search */

export interface SearchPanelProps {
  locale: Locale;
  year: number;
  onPick: (hit: SearchHit) => void;
}

export function SearchPanel({ locale, year, onPick }: SearchPanelProps) {
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const query = term.trim();
    if (query.length < 2) {
      setHits(null);
      return;
    }
    const controller = new AbortController();
    setBusy(true);
    const timer = setTimeout(() => {
      fetch(`/api/v1/search?q=${encodeURIComponent(query)}&locale=${locale}&limit=12&t=${year}`, {
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) throw new Error(String(response.status));
          const body = (await response.json()) as { data: SearchHit[] };
          setHits(body.data);
          setError(null);
        })
        .catch((cause: unknown) => {
          if ((cause as Error).name !== "AbortError") setError(t(locale, "ui.error.backend"));
        })
        .finally(() => setBusy(false));
    }, 220);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [term, locale, year]);

  return (
    <section className="panel">
      <header className="panel-title">
        <span>{t(locale, "ui.search.title")}</span>
        {busy ? <span className="spinner" /> : null}
      </header>
      <div style={{ padding: 10 }}>
        <input
          className="field"
          type="search"
          value={term}
          placeholder={t(locale, "ui.search.placeholder")}
          onChange={(event) => setTerm(event.target.value)}
          aria-label={t(locale, "ui.search.title")}
        />
      </div>
      {error ? <div className="legend" style={{ color: "#e8836f" }}>{error}</div> : null}
      {hits && hits.length === 0 ? <div className="legend">{t(locale, "ui.search.empty")}</div> : null}
      {hits && hits.length > 0 ? (
        <div style={{ maxHeight: 260, overflowY: "auto" }}>
          {hits.map((hit) => (
            <button type="button" key={hit.id} className="hit" onClick={() => onPick(hit)}>
              <span className="hit-title">{hit.label}</span>
              <span className="hit-sub">
                <span className="dot" style={{ background: colorForLayer(hit.layer) }} />
                {hit.label_secondary}
                {hit.t_display ? <span className="chip">{hit.t_display}</span> : null}
                {hit.has_disagreements ? <span className="chip disputed">?</span> : null}
              </span>
              {hit.snippet ? <div className="snippet">{hit.snippet}</div> : null}
            </button>
          ))}
        </div>
      ) : null}
      <div className="legend" style={{ paddingTop: 6 }}>
        {t(locale, "ui.search.hint")}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ legend + certainty */

export function LegendPanel({ locale }: { locale: Locale }) {
  const rows: { certainty: "exact" | "approximate" | "uncertain" | "reconstructed"; className: string }[] = [
    { certainty: "exact", className: "legend-swatch" },
    { certainty: "approximate", className: "legend-swatch" },
    { certainty: "uncertain", className: "legend-swatch dashed" },
    { certainty: "reconstructed", className: "legend-swatch dashed" },
  ];
  return (
    <section className="panel">
      <header className="panel-title">
        <span>{locale === "fa" ? "راهنمای دقت" : "Certainty legend"}</span>
      </header>
      <div className="legend">
        {rows.map((row) => (
          <div className="legend-row" key={row.certainty}>
            <span className={row.className} />
            <span>{certaintyLabel(locale, row.certainty)}</span>
          </div>
        ))}
        <div className="legend-row">
          <span className="legend-swatch hollow" />
          <span>{t(locale, "ui.entity.derivedLocus")}</span>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ coverage */

export function CoveragePanel({
  locale,
  coverage,
  gaps,
  disclaimer,
}: {
  locale: Locale;
  coverage: CoverageStats;
  gaps: string[];
  disclaimer: string;
}) {
  const rows = Object.entries(coverage).filter(([, value]) => typeof value === "number" && (value as number) > 0);
  return (
    <section className="panel">
      <header className="panel-title">
        <span>{t(locale, "ui.coverage.title")}</span>
      </header>
      <div className="legend">
        <div className="row-wrap" style={{ marginBottom: 6 }}>
          {rows.map(([key, value]) => (
            <span className="chip" key={key}>
              {key.replace(/_/g, " ")} <strong style={{ color: "var(--text)" }}>{formatNumber(Number(value), locale)}</strong>
            </span>
          ))}
        </div>
        <div className="callout" style={{ marginBottom: 6 }}>
          {disclaimer}
        </div>
        {coverage.disputed_assertions ? (
          <div className="legend-row">
            <span className="chip disputed">{t(locale, "ui.coverage.disputed", { count: coverage.disputed_assertions })}</span>
          </div>
        ) : null}
        {coverage.provisional_geometries ? (
          <div className="legend-row">
            <span className="chip warn">{t(locale, "ui.coverage.provisional", { count: coverage.provisional_geometries })}</span>
          </div>
        ) : null}
        {gaps.length > 0 ? (
          <details style={{ marginTop: 6 }}>
            <summary style={{ cursor: "pointer", color: "var(--text-dim)" }}>
              {t(locale, "ui.coverage.gaps")} ({gaps.length})
            </summary>
            <ul style={{ margin: "6px 0 0", paddingInlineStart: 18, fontSize: 11, color: "var(--text-faint)" }}>
              {gaps.slice(0, 12).map((gap) => (
                <li key={gap} style={{ fontFamily: "var(--font-mono)" }}>
                  {gap}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ status bar */

export function StatusBar({
  locale,
  zoomLevel,
  featureCount,
  payloadBytes,
  truncated,
  driver,
  hovered,
  delivery,
  dataRevision,
}: {
  locale: Locale;
  zoomLevel: ZoomLevelInfo | null;
  featureCount: number;
  payloadBytes: number;
  truncated: boolean;
  driver: string;
  hovered: FeatureProperties | null;
  delivery: TilesDelivery;
  dataRevision: string | null;
}) {
  return (
    <div className="statusbar">
      {zoomLevel ? (
        <span className="status-pill">
          <strong>{zoomLevel.level}</strong> · {zoomLevel.label}
          {zoomLevel.description ? ` — ${zoomLevel.description}` : ""}
        </span>
      ) : null}
      {delivery === "geojson" ? (
        <>
          <span className="status-pill">
            <strong>{formatNumber(featureCount, locale)}</strong> {locale === "fa" ? "عارضه" : "features"}
          </span>
          <span className="status-pill">
            {t(locale, "ui.features.payload", { kb: (payloadBytes / 1024).toFixed(0) })}
          </span>
          {truncated ? <span className="status-pill warn">{t(locale, "ui.features.truncated")}</span> : null}
        </>
      ) : (
        <>
          {/* Tiles are counted by the archive, not by the viewport: saying "0 features" here would
              be a lie about what is on screen. */}
          <span className="status-pill">
            <strong>{t(locale, delivery === "pmtiles" ? "ui.source.pmtiles" : "ui.source.dynamic")}</strong>
          </span>
          {dataRevision ? (
            <span className="status-pill" style={{ fontFamily: "var(--font-mono)" }}>
              {t(locale, "ui.status.revision")} {dataRevision.slice(0, 8)}
            </span>
          ) : null}
        </>
      )}
      {hovered ? (
        <span className="status-pill">
          <strong>{hovered.label}</strong>
          {hovered.t_display ? ` · ${hovered.t_display}` : ""}
          {hovered.certainty ? ` · ${certaintyLabel(locale, hovered.certainty)}` : ""}
        </span>
      ) : null}
      <span className="status-pill" style={{ fontFamily: "var(--font-mono)" }}>
        {driver}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ locale + nav */

export function LocaleSwitcher({ locale, search }: { locale: Locale; search: string }) {
  const router = useRouter();
  const other: Locale = locale === "fa" ? "en" : "fa";
  return (
    <button
      type="button"
      className="btn"
      onClick={() => {
        const path = typeof window === "undefined" ? `/${other}` : window.location.pathname.replace(/^\/(fa|en)/, `/${other}`);
        router.push(`${path}${search}`);
      }}
      title={other === "fa" ? "فارسی" : "English"}
    >
      {other === "fa" ? "فارسی" : "English"}
    </button>
  );
}

export function NavLinks({ locale, meta }: { locale: Locale; meta: MetaResponse | null }) {
  return (
    <nav className="panel row" style={{ padding: "6px 8px", gap: 4 }}>
      <a className="btn btn-ghost" href={`/${locale}/`}>{t(locale, "ui.nav.atlas")}</a>
      <a className="btn btn-ghost" href={`/${locale}/articles`}>{t(locale, "ui.nav.articles")}</a>
      <a className="btn btn-ghost" href={`/${locale}/sources`}>{t(locale, "ui.nav.sources")}</a>
      <a className="btn btn-ghost" href={`/${locale}/about`}>{t(locale, "ui.nav.about")}</a>
      {meta ? <span className="chip" style={{ marginInlineStart: 6 }}>{meta.api_version ? `v${meta.api_version}` : "v1"}</span> : null}
    </nav>
  );
}
