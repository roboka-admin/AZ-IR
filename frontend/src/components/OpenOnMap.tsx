"use client";

/**
 * Article -> Map deep link.
 *
 * The `map_state` is authored with the article (centre, zoom, time window, layers) and arrives
 * from the API; this component only turns it into a navigation, so no view state is invented in
 * the frontend (AGENTS.md rule 15).
 */

import { useRouter } from "next/navigation";

import { t } from "@/lib/i18n";
import type { Locale, MapState } from "@/lib/types";

export function mapStateToQuery(mapState: MapState, entityId?: string): string {
  const params = new URLSearchParams();
  params.set("c", `${mapState.center[0].toFixed(4)},${mapState.center[1].toFixed(4)}`);
  params.set("z", String(mapState.zoom));
  const time = mapState.time ?? {};
  if (time.from !== undefined && time.to !== undefined) {
    params.set("from", String(time.from));
    params.set("to", String(time.to));
    params.set("mode", time.mode ?? "overlaps");
  } else if (time.year !== undefined) {
    params.set("t", String(time.year));
  }
  if (mapState.layers && mapState.layers.length > 0) params.set("l", mapState.layers.join(","));
  if (entityId) params.set("entity", entityId);
  return params.toString();
}

export default function OpenOnMap({
  locale,
  mapState,
  entityId,
  label,
}: {
  locale: Locale;
  mapState: MapState;
  entityId?: string;
  label?: string;
}) {
  const router = useRouter();
  return (
    <button
      type="button"
      className="btn btn-primary"
      onClick={() => router.push(`/${locale}?${mapStateToQuery(mapState, entityId)}`)}
    >
      {label ?? t(locale, "ui.entity.showOnMap")}
    </button>
  );
}
