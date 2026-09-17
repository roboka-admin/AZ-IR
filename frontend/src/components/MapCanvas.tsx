"use client";

/**
 * MapLibre host.
 *
 * Deliberately dumb: it owns the map instance, the basemap and the interaction events, and
 * nothing else. Which features exist, when they existed and how certain we are about them is
 * decided by the API (AGENTS.md rules 5 and 20: the map is presentation, not truth).
 */

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import {
  BASEMAP_STYLE_URL,
  FALLBACK_FONT,
  LIBERTY_FONT,
  dataLayerIds,
  ensureAtlasLayers,
  ensureFallbackLayers,
  fallbackStyle,
  restoreAtlasLayers,
  setDelivery,
  type VectorBinding,
} from "@/lib/mapStyle";
import type { FeatureProperties } from "@/lib/types";

export interface MapCanvasProps {
  center: [number, number]; // [lat, lon]
  zoom: number;
  /** Tiles, when the API offers them. `null` keeps the GeoJSON viewport delivery. */
  tiles?: VectorBinding | null;
  maxBounds?: [number, number, number, number]; // [w, s, e, n]
  onReady: (map: maplibregl.Map) => void;
  onViewChange: (view: { center: [number, number]; zoom: number; bbox: [number, number, number, number] }) => void;
  onFeatureClick: (props: FeatureProperties, lngLat: { lat: number; lng: number }) => void;
  onBackgroundClick: () => void;
  onFeatureHover: (props: FeatureProperties | null) => void;
}

const PROBE_TIMEOUT_MS = 4000;
const RTL_TEXT_PLUGIN_URL = "/vendor/mapbox-gl-rtl-text/mapbox-gl-rtl-text.js";

/** Register Arabic shaping and bidirectional-text support once, loading it only when needed. */
async function ensureRtlTextSupport(): Promise<void> {
  if (maplibregl.getRTLTextPluginStatus() !== "unavailable") return;
  await maplibregl.setRTLTextPlugin(RTL_TEXT_PLUGIN_URL, true);
}

/**
 * Resolve the basemap before constructing the map.
 *
 * A free public basemap is a third-party dependency, so we probe it once: if it is unreachable
 * (offline demo, blocked CDN) we fall back to a local blank style and the historical layers still
 * render. Transient tile errors later on never destroy a working basemap.
 */
async function resolveBasemap(): Promise<{ style: string | object; font: string[]; offline: boolean }> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
    const response = await fetch(BASEMAP_STYLE_URL, { signal: controller.signal });
    clearTimeout(timer);
    if (!response.ok) throw new Error(`basemap ${response.status}`);
    return { style: BASEMAP_STYLE_URL, font: LIBERTY_FONT, offline: false };
  } catch {
    return { style: fallbackStyle(), font: FALLBACK_FONT, offline: true };
  }
}

export default function MapCanvas({
  center,
  zoom,
  tiles = null,
  maxBounds,
  onReady,
  onViewChange,
  onFeatureClick,
  onBackgroundClick,
  onFeatureHover,
}: MapCanvasProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const fontRef = useRef<string[]>(LIBERTY_FONT);
  const tilesRef = useRef<VectorBinding | null>(tiles);
  tilesRef.current = tiles;
  const handlers = useRef({ onViewChange, onFeatureClick, onBackgroundClick, onFeatureHover });
  handlers.current = { onViewChange, onFeatureClick, onBackgroundClick, onFeatureHover };

  useEffect(() => {
    let cancelled = false;
    let map: maplibregl.Map | null = null;

    void (async () => {
      try {
        await ensureRtlTextSupport();
      } catch (cause) {
        // A blocked plugin CDN must not prevent the map and its unshaped fallback labels loading.
        console.warn("RTL text plugin unavailable", cause);
      }
      const resolved = await resolveBasemap();
      if (cancelled || !container.current) return;
      fontRef.current = resolved.font;

      map = new maplibregl.Map({
        container: container.current,
        style: resolved.style as never,
        center: [center[1], center[0]],
        zoom,
        minZoom: 2,
        maxZoom: 18,
        maxBounds,
        attributionControl: { compact: true },
      });
      mapRef.current = map;

      const emitView = () => {
        const bounds = map?.getBounds();
        if (!map || !bounds) return;
        handlers.current.onViewChange({
          center: [map.getCenter().lat, map.getCenter().lng],
          zoom: map.getZoom(),
          bbox: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()],
        });
      };

      const restore = () => {
        if (!map) return;
        // A style swap drops every custom source and layer: put them back, in the delivery mode
        // that was chosen, and replay the last GeoJSON payload if there was one.
        restoreAtlasLayers(map, fontRef.current);
      };

      map.on("load", () => {
        const ready = map;
        if (!ready) return;
        if (resolved.offline && maxBounds) ensureFallbackLayers(ready, maxBounds);
        // The first add decides the delivery; later swaps go through setDelivery below.
        ensureAtlasLayers(ready, fontRef.current, tilesRef.current);
        ready.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
        ready.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "metric" }), "bottom-right");
        for (const layer of dataLayerIds(ready)) {
          ready.on("click", layer, ((event: maplibregl.MapLayerMouseEvent) => {
            const feature = event.features?.[0];
            if (!feature) return;
            handlers.current.onFeatureClick(feature.properties as unknown as FeatureProperties, {
              lat: event.lngLat.lat,
              lng: event.lngLat.lng,
            });
          }) as never);
          ready.on("mousemove", layer, ((event: maplibregl.MapLayerMouseEvent) => {
            const feature = event.features?.[0];
            handlers.current.onFeatureHover(feature ? (feature.properties as unknown as FeatureProperties) : null);
          }) as never);
          ready.on("mouseenter", layer, () => {
            ready.getCanvas().style.setProperty("cursor", "pointer");
          });
          ready.on("mouseleave", layer, () => {
            ready.getCanvas().style.setProperty("cursor", "");
            handlers.current.onFeatureHover(null);
          });
        }
        ready.on("click", (event) => {
          const hits = ready.queryRenderedFeatures(event.point, { layers: dataLayerIds(ready) });
          if (hits.length === 0) handlers.current.onBackgroundClick();
        });
        onReady(ready);
        emitView();
      });

      map.on("styledata", restore);
      map.on("moveend", emitView);
      map.on("zoomend", emitView);

      // Missing glyphs would spam the console without changing anything the reader sees.
      map.on("error", (event) => {
        const message = String(event?.error?.message ?? "");
        if (/glyph|font/i.test(message) && map?.getLayer("azir-label")) {
          map.setLayoutProperty("azir-label", "visibility", "none");
        }
      });
    })();

    return () => {
      cancelled = true;
      map?.remove();
      mapRef.current = null;
    };
    // The map is created once; later view changes are driven imperatively by the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Switching between tiles and GeoJSON is a live operation: no remount, no lost viewport.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) {
      if (map) setDelivery(map, tiles);
      return;
    }
    setDelivery(map, tiles, fontRef.current);
  }, [tiles]);

  return <div ref={container} className="map-wrap" aria-label="atlas map" />;
}
