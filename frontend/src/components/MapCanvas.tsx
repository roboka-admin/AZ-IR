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
  DATA_LAYERS,
  FALLBACK_FONT,
  LIBERTY_FONT,
  ensureAtlasLayers,
  ensureFallbackLayers,
  fallbackStyle,
  replayAtlasData,
} from "@/lib/mapStyle";
import type { FeatureProperties } from "@/lib/types";

export interface MapCanvasProps {
  center: [number, number]; // [lat, lon]
  zoom: number;
  maxBounds?: [number, number, number, number]; // [w, s, e, n]
  onReady: (map: maplibregl.Map) => void;
  onViewChange: (view: { center: [number, number]; zoom: number; bbox: [number, number, number, number] }) => void;
  onFeatureClick: (props: FeatureProperties, lngLat: { lat: number; lng: number }) => void;
  onBackgroundClick: () => void;
  onFeatureHover: (props: FeatureProperties | null) => void;
}

const PROBE_TIMEOUT_MS = 4000;

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
  const handlers = useRef({ onViewChange, onFeatureClick, onBackgroundClick, onFeatureHover });
  handlers.current = { onViewChange, onFeatureClick, onBackgroundClick, onFeatureHover };

  useEffect(() => {
    let cancelled = false;
    let map: maplibregl.Map | null = null;

    void (async () => {
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
        // A style swap drops every custom source/layer: put them back and replay the last data.
        ensureAtlasLayers(map, fontRef.current);
        replayAtlasData(map);
      };

      map.on("load", () => {
        if (map && resolved.offline && maxBounds) ensureFallbackLayers(map, maxBounds);
        restore();
        map?.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
        map?.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "metric" }), "bottom-right");
        for (const layer of DATA_LAYERS) {
          if (!map?.getLayer(layer)) continue;
          map.on("click", layer, ((event: maplibregl.MapLayerMouseEvent) => {
            const feature = event.features?.[0];
            if (!feature) return;
            handlers.current.onFeatureClick(feature.properties as unknown as FeatureProperties, {
              lat: event.lngLat.lat,
              lng: event.lngLat.lng,
            });
          }) as never);
          map.on("mousemove", layer, ((event: maplibregl.MapLayerMouseEvent) => {
            const feature = event.features?.[0];
            handlers.current.onFeatureHover(feature ? (feature.properties as unknown as FeatureProperties) : null);
          }) as never);
          map.on("mouseenter", layer, () => {
            map?.getCanvas().style.setProperty("cursor", "pointer");
          });
          map.on("mouseleave", layer, () => {
            map?.getCanvas().style.setProperty("cursor", "");
            handlers.current.onFeatureHover(null);
          });
        }
        map?.on("click", (event) => {
          const hits = map?.queryRenderedFeatures(event.point, {
            layers: DATA_LAYERS.filter((layer) => Boolean(map?.getLayer(layer))),
          });
          if (!hits || hits.length === 0) handlers.current.onBackgroundClick();
        });
        if (map) onReady(map);
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

  return <div ref={container} className="map-wrap" aria-label="atlas map" />;
}
