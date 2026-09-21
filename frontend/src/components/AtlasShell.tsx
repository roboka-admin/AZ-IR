"use client";

/**
 * The atlas screen: map + timeline + panels, wired together.
 *
 * All state that defines "what am I looking at" (centre, zoom, year, calendar, mode, layers,
 * selection) lives in the URL, so any view is shareable and reproducible. All *content* comes from
 * the API: this component never contains a year, a name or a boundary of its own
 * (AGENTS.md rules 15 and 19).
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type maplibregl from "maplibre-gl";

import EntityDrawer from "./EntityDrawer";
import MapCanvas from "./MapCanvas";
import { CoveragePanel, LayersPanel, LegendPanel, LocaleSwitcher, NavLinks, PoliticalLegendPanel, SearchPanel, StatusBar } from "./Panels";
import TimelinePanel from "./TimelinePanel";
import { ApiError, getCalendarYear, getEntity, getFeatures, getMeta, getTilesIndex, getTimeline, roundZoom } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  setSelectedFeature,
  setLayerVisibility,
  setTemporalWindow,
  updateAtlasData,
  type VectorBinding,
} from "@/lib/mapStyle";
import { resolveTiles, type ResolvedTiles } from "@/lib/tiles";
import { parseAtlasState, serializeAtlasState, type AtlasState } from "@/lib/atlasState";
import {
  centerTimelineWindow,
  clampTimelineYear,
  moveRangeToYear,
  normalizeYearRange,
  rangeMidpoint,
} from "@/lib/timeline";
import type {
  CalendarCode,
  EntityDetail,
  FeatureCollection,
  FeatureProperties,
  Locale,
  MetaResponse,
  SearchHit,
  TemporalMode,
  TimelineBucket,
  TilesIndex,
  ZoomLevelInfo,
} from "@/lib/types";

const FETCH_DEBOUNCE_MS = 180;

export default function AtlasShell({ locale }: { locale: Locale }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [state, setState] = useState<AtlasState | null>(null);
  const [view, setView] = useState<{ center: [number, number]; zoom: number; bbox: [number, number, number, number] } | null>(null);
  const [collection, setCollection] = useState<FeatureCollection | null>(null);
  const [buckets, setBuckets] = useState<TimelineBucket[]>([]);
  const [timelineWindow, setTimelineWindow] = useState<[number, number]>([1200, 1800]);
  const [displayYear, setDisplayYear] = useState<number | null>(null);
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [hovered, setHovered] = useState<FeatureProperties | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [tilesIndex, setTilesIndex] = useState<TilesIndex | null>(null);
  const [tiles, setTiles] = useState<ResolvedTiles | null>(null);

  const mapRef = useRef<maplibregl.Map | null>(null);
  const stateRef = useRef<AtlasState | null>(null);
  stateRef.current = state;

  /* ---------------------------------------------------------- bootstrap */

  useEffect(() => {
    const controller = new AbortController();
    getMeta(locale, controller.signal)
      .then((payload) => {
        setMeta(payload);
        setState(parseAtlasState(new URLSearchParams(searchParams.toString()), payload));
        const floor = payload.timeline.floor;
        const ceil = payload.timeline.ceil;
        const initial = parseAtlasState(new URLSearchParams(searchParams.toString()), payload);
        setTimelineWindow(centerTimelineWindow(initial.year, Math.min(600, ceil - floor), floor, ceil));
      })
      .catch((cause: unknown) => {
        if ((cause as Error).name !== "AbortError") {
          setFatal(cause instanceof ApiError ? cause.problem.detail : t(locale, "ui.error.backend"));
        }
      });
    return () => controller.abort();
    // Bootstrapping happens once per locale; the search params are read at mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locale]);

  /* ---------------------------------------------------------- URL sync */

  useEffect(() => {
    if (!state || !meta) return;
    const serialized = serializeAtlasState(state, meta);
    const current = searchParams.toString();
    if (serialized === current) return;
    const timer = setTimeout(() => {
      router.replace(`${pathname}?${serialized}`, { scroll: false });
    }, 260);
    return () => clearTimeout(timer);
  }, [state, meta, pathname, router, searchParams]);

  /* ---------------------------------------------------------- tile delivery */

  // What the API offers. A missing or disabled tile endpoint is not an error: the map then simply
  // keeps using the GeoJSON viewport, which always works.
  useEffect(() => {
    const controller = new AbortController();
    getTilesIndex(locale, controller.signal)
      .then((payload) => setTilesIndex(payload.data))
      .catch((cause: unknown) => {
        if ((cause as Error).name !== "AbortError") setTilesIndex(null);
      });
    return () => controller.abort();
  }, [locale]);

  useEffect(() => {
    let cancelled = false;
    void resolveTiles(tilesIndex, state?.source ?? "auto").then((resolved) => {
      if (!cancelled) setTiles(resolved);
    });
    return () => {
      cancelled = true;
    };
  }, [tilesIndex, state?.source]);

  const tilesBinding = useMemo<VectorBinding | null>(
    () => (tiles?.spec ? { spec: tiles.spec, sourceLayers: tiles.sourceLayers } : null),
    [tiles],
  );
  const delivery = tilesBinding ? (tiles?.mode ?? "dynamic") : "geojson";

  /* ---------------------------------------------------------- features */

  const activeLayers = useMemo<string[]>(() => {
    if (!meta) return [];
    if (state?.layers && state.layers.length > 0) return state.layers;
    return meta.layers.filter((layer) => layer.default_on).map((layer) => layer.id);
  }, [meta, state?.layers]);

  useEffect(() => {
    if (!meta || !state || !view || !mapReady) return;
    // In tile mode the map already has everything: the archive holds the whole region, and the
    // timeline narrows it locally. Fetching a viewport on top of that would pay twice for one map.
    if (tilesBinding) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      const params = {
        bbox: view.bbox.map((value) => value.toFixed(3)).join(","),
        zoom: roundZoom(view.zoom),
        // Timeline state is canonical Gregorian. The selected calendar changes presentation only,
        // so switching calendars never jumps to a different historical instant.
        mode: state.mode,
        layers: activeLayers.join(","),
        locale,
        fields: "default" as const,
        limit: 400,
        ...(state.timelineActive
          ? state.span
            ? { from: state.span[0], to: state.span[1] }
            : { t: state.year }
          : { all_time: true }),
      };
      getFeatures(params, controller.signal)
        .then((payload) => {
          setCollection(payload);
          const map = mapRef.current;
          if (map) updateAtlasData(map, payload);
        })
        .catch((cause: unknown) => {
          if ((cause as Error).name !== "AbortError") console.error("features failed", cause);
        });
    }, FETCH_DEBOUNCE_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [meta, state, view, activeLayers, locale, mapReady, tilesBinding]);

  /* ---------------------------------------------------------- presentation filters */

  // Layer visibility and selection now live in their own effects: they apply to both deliveries,
  // whereas the payload above only exists in GeoJSON mode.
  useEffect(() => {
    const map = mapRef.current;
    if (map && mapReady) setLayerVisibility(map, activeLayers);
  }, [activeLayers, mapReady, tilesBinding]);

  useEffect(() => {
    if (!mapReady) return;
    setSelectedFeature(mapRef.current, state?.entity ?? null);
  }, [state?.entity, mapReady, tilesBinding]);

  /** The map and tiles always filter on canonical normalized years. Calendar choice is display-only. */
  useEffect(() => {
    if (!state || !mapReady) return;
    setTemporalWindow(mapRef.current, !state.timelineActive
      ? { from: null, to: null, mode: "overlaps" }
      : state.span
        ? { from: state.span[0], to: state.span[1], mode: state.mode }
        : { from: state.year, to: state.year, mode: state.mode });
  }, [state, mapReady, tilesBinding]);

  // Conversion remains an auditable backend/domain operation; the browser only renders its result.
  useEffect(() => {
    if (!state) return;
    const controller = new AbortController();
    getCalendarYear(state.year, state.calendar, controller.signal)
      .then((payload) => setDisplayYear(payload.data.year))
      .catch((cause: unknown) => {
        if ((cause as Error).name !== "AbortError") console.error("calendar display failed", cause);
      });
    return () => controller.abort();
    // Only time/calendar changes require conversion; map pans and layer toggles must not refetch it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.year, state?.calendar]);

  /* ---------------------------------------------------------- timeline */

  useEffect(() => {
    if (!meta || !view) return;
    const controller = new AbortController();
    const [from, to] = timelineWindow;
    getTimeline(
      {
        from,
        to,
        bbox: view.bbox.map((value) => value.toFixed(3)).join(","),
        layers: activeLayers.join(","),
        locale,
        cal: state?.calendar,
      },
      controller.signal,
    )
      .then((payload) => setBuckets(payload.data))
      .catch((cause: unknown) => {
        if ((cause as Error).name !== "AbortError") console.error("timeline failed", cause);
      });
    return () => controller.abort();
  }, [meta, view, timelineWindow, activeLayers, locale, state?.calendar]);

  /* ---------------------------------------------------------- selection */

  const openEntity = useCallback(
    async (entityType: string, idOrSlug: string, revealOnActiveTimeline = false) => {
      setDetailLoading(true);
      try {
        const payload = await getEntity(entityType, idOrSlug, locale);
        setDetail(payload);
        const temporal = payload.temporal;
        const revealYear = temporal && temporal.year_from !== null
          ? Math.round((temporal.year_from + (temporal.year_to ?? temporal.year_from)) / 2)
          : null;
        setState((previous) => {
          if (!previous) return previous;
          if (!revealOnActiveTimeline || !previous.timelineActive || revealYear === null) {
            return { ...previous, entity: payload.id };
          }
          return { ...previous, entity: payload.id, year: revealYear, span: null, mode: "at", playing: false };
        });
        if (revealOnActiveTimeline && stateRef.current?.timelineActive && revealYear !== null && meta) {
          setTimelineWindow((current) => centerTimelineWindow(
            revealYear,
            current[1] - current[0],
            meta.timeline.floor,
            meta.timeline.ceil,
          ));
        }
        const center = payload.geometry?.center;
        if (center && mapRef.current) {
          mapRef.current.easeTo({ center: [center[0], center[1]], zoom: Math.max(mapRef.current.getZoom(), 8), duration: 700 });
        }
        setSelectedFeature(mapRef.current, payload.id);
      } catch (cause) {
        if (cause instanceof ApiError) setFatal(cause.problem.detail);
      } finally {
        setDetailLoading(false);
      }
    },
    [locale, meta],
  );

  useEffect(() => {
    if (!state?.entity || !meta) return;
    if (detail?.id === state.entity) return;
    const type = typeFromId(state.entity);
    if (type) void openEntity(type, state.entity);
  }, [state?.entity, meta, detail?.id, openEntity]);

  const closeDetail = useCallback(() => {
    setDetail(null);
    setState((previous) => (previous ? { ...previous, entity: null } : previous));
    if (mapRef.current) setSelectedFeature(mapRef.current, null);
  }, []);

  const setYear = useCallback((year: number) => {
    if (!meta || !state) return;
    const clamped = clampTimelineYear(year, meta);
    const nextSpan = state.span
      ? moveRangeToYear(state.span, clamped, meta.timeline.floor, meta.timeline.ceil)
      : null;
    const cursor = nextSpan ? rangeMidpoint(nextSpan) : clamped;
    setState((previous) => (previous ? { ...previous, year: cursor, span: nextSpan, playing: false } : previous));
    const [from, to] = timelineWindow;
    if (cursor < from || cursor > to) {
      setTimelineWindow(centerTimelineWindow(cursor, to - from, meta.timeline.floor, meta.timeline.ceil));
    }
  }, [meta, state, timelineWindow]);

  /* ---------------------------------------------------------- playback */

  useEffect(() => {
    if (!state?.playing || !meta) return;
    const [from, to] = timelineWindow;
    const step = Math.max(1, Math.round((to - from) / 140));
    const timer = setInterval(() => {
      const current = stateRef.current;
      if (!current) return;
      let next = current.year + step;
      if (next > to) {
        next = from;
      }
      const nextSpan = current.span
        ? moveRangeToYear(current.span, next, meta.timeline.floor, meta.timeline.ceil)
        : null;
      const cursor = nextSpan ? rangeMidpoint(nextSpan) : next;
      setState({ ...current, year: cursor, span: nextSpan });
      if (cursor < timelineWindow[0] || cursor > timelineWindow[1]) {
        setTimelineWindow(
          centerTimelineWindow(cursor, timelineWindow[1] - timelineWindow[0], meta.timeline.floor, meta.timeline.ceil),
        );
      }
    }, 90);
    return () => clearInterval(timer);
  }, [state?.playing, meta, timelineWindow]);

  /* ---------------------------------------------------------- keyboard */

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      const current = stateRef.current;
      if (!current || !meta) return;
      const big = event.shiftKey ? 25 : 1;
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        const direction = event.key === "ArrowRight" ? 1 : -1;
        // In RTL the arrow keys keep their physical meaning: right is always later in time.
        event.preventDefault();
        setYear(clampTimelineYear(current.year + direction * big, meta));
      } else if (event.key === " ") {
        event.preventDefault();
        patch({ playing: !current.playing });
      } else if (event.key === "Escape") {
        closeDetail();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [meta, closeDetail, setYear]);

  /* ---------------------------------------------------------- helpers */

  function patch(changes: Partial<AtlasState>) {
    setState((previous) => (previous ? { ...previous, ...changes } : previous));
  }

  function onSearchPick(hit: SearchHit) {
    patch({ query: null });
    if (hit.center && mapRef.current) {
      mapRef.current.flyTo({ center: [hit.center[1], hit.center[0]], zoom: Math.max(9, hit.center ? 9 : 6), duration: 900 });
    }
    void openEntity(hit.entity_type, hit.slug ?? hit.id, true);
  }

  const zoomLevel: ZoomLevelInfo | null = useMemo(() => {
    if (!meta || !view) return null;
    return (
      meta.zoom_levels.find((level) => view.zoom >= level.min_zoom && view.zoom <= level.max_zoom) ??
      meta.zoom_levels[meta.zoom_levels.length - 1] ??
      null
    );
  }, [meta, view]);

  if (fatal && !meta) {
    return (
      <div className="doc-page">
        <div className="doc-container">
          <h1 className="doc-title">{t(locale, "ui.error.title")}</h1>
          <p className="doc-lede">{fatal}</p>
          <button type="button" className="btn btn-primary" onClick={() => router.refresh()}>
            {t(locale, "ui.retry")}
          </button>
        </div>
      </div>
    );
  }

  if (!meta || !state) {
    return (
      <div className="app">
        <div className="map-wrap" style={{ background: "var(--ink-900)" }} />
        <div className="statusbar">
          <span className="status-pill">
            <span className="spinner" style={{ display: "inline-block", verticalAlign: -2, marginInlineEnd: 6 }} />
            {t(locale, "ui.loading")}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <MapCanvas
        center={state.center}
        zoom={state.zoom}
        tiles={tilesBinding}
        maxBounds={meta.study_area.bbox}
        onReady={(map) => {
          mapRef.current = map;
          setMapReady(true);
        }}
        onViewChange={(next) => {
          setView(next);
          patch({ center: next.center, zoom: next.zoom });
        }}
        onFeatureClick={(props) => void openEntity(props.entity_type, props.slug ?? props.id)}
        onBackgroundClick={() => closeDetail()}
        onFeatureHover={setHovered}
      />

      <header className="header">
        <div className="brand">
          <strong>{t(locale, "app.shortTitle")}</strong>
          <span>{meta.study_area.name}</span>
        </div>
        <NavLinks locale={locale} meta={meta} />
        <div className="header-spacer" />
        <LocaleSwitcher locale={locale} search={`?${serializeAtlasState(state, meta)}`} />
      </header>

      <div className="stack">
        <SearchPanel locale={locale} onPick={onSearchPick} />
        <LayersPanel
          locale={locale}
          layers={meta.layers}
          selected={new Set(activeLayers)}
          onToggle={(id) => {
            const next = new Set(activeLayers);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            patch({ layers: [...next] });
          }}
          onOnly={(id) => patch({ layers: [id] })}
          source={state.source}
          delivery={delivery}
          archiveLocales={tilesIndex?.archive_locales ?? []}
          onSourceChange={(source) => patch({ source })}
        />
        {activeLayers.includes("political_entities") ? (
          <PoliticalLegendPanel locale={locale} entities={meta.political_entities} />
        ) : null}
        <LegendPanel locale={locale} />
        <CoveragePanel
          locale={locale}
          coverage={meta.coverage}
          gaps={collection?.meta.coverage_gaps ?? []}
          disclaimer={meta.disclaimer.borders}
        />
      </div>

      {detail ? (
        <EntityDrawer
          detail={detail}
          locale={locale}
          loading={detailLoading}
          onClose={closeDetail}
          onOpenEntity={(type, id) => void openEntity(type, id)}
          onFlyTo={(center, zoom) => mapRef.current?.flyTo({ center: [center[1], center[0]], zoom, duration: 800 })}
        />
      ) : null}

      <StatusBar
        locale={locale}
        zoomLevel={zoomLevel}
        featureCount={collection?.meta.returned ?? 0}
        payloadBytes={collection?.meta.payload_bytes ?? 0}
        truncated={collection?.meta.truncated ?? false}
        driver={collection?.meta.driver ?? tilesIndex?.archive?.driver ?? meta.driver ?? "—"}
        hovered={hovered}
        delivery={delivery}
        dataRevision={tiles?.archive?.data_revision ?? null}
      />

      <TimelinePanel
        locale={locale}
        meta={meta}
        year={state.year}
        calendar={state.calendar}
        mode={state.mode}
        span={state.span}
        window={timelineWindow}
        buckets={buckets}
        playing={state.playing}
        calendarDisplay={displayYear === null ? null : {
          from: displayYear,
          to: displayYear,
          calendar: state.calendar,
        }}
        active={state.timelineActive}
        onActiveChange={(timelineActive) => patch({ timelineActive, playing: false })}
        onYearChange={setYear}
        onWindowChange={setTimelineWindow}
        onCalendarChange={(calendar: CalendarCode) => patch({ calendar })}
        onModeChange={(mode: TemporalMode) => patch({ mode })}
        onSpanChange={(span) => {
          if (!span) {
            patch({ span: null, mode: "at" });
            return;
          }
          const normalized = normalizeYearRange(span, meta.timeline.floor, meta.timeline.ceil);
          patch({ span: normalized, year: rangeMidpoint(normalized), mode: "overlaps" });
        }}
        onTogglePlay={() => patch({ playing: !state.playing })}
        onOpenEntity={(type, id) => void openEntity(type, id)}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ utilities */

/** `plc_…` -> `place`. The prefix table is an id convention (ADR-0008), not historical data. */
function typeFromId(id: string): string | null {
  const prefix = id.slice(0, 3);
  const table: Record<string, string> = {
    plc: "place",
    prs: "person",
    evt: "event",
    pol: "political_entity",
    art: "article",
    prd: "period",
    src: "source",
  };
  return table[prefix] ?? null;
}
