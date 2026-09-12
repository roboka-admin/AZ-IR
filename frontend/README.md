# frontend — AZ-IR

Next.js 15 (App Router) + React 19 + TypeScript strict + MapLibre GL JS.
Persian (RTL) is the primary language, English the second; both are first-class, not translations
of an afterthought.

```bash
npm ci
npm run dev         # http://localhost:3000  (proxies /api/* to the backend)
npm run typecheck   # tsc --noEmit
npm run lint        # eslint, zero warnings allowed
npm run build       # production build (needs a reachable API: server pages render from it)
```

`AZIR_BACKEND_URL` (default `http://127.0.0.1:8000`) is the **server-side** target of the
`/api/*` rewrite. Browsers never call the backend directly, so there is no CORS surface and no
second origin to deploy (AGENTS.md rule 1).

## Rules this codebase lives by

1. **No historical data in components.** Every name, date, kind, layer, colour key and certainty
   arrives from the API. If a component needs a list, it comes from `/meta` (rule 17).
2. **The map is presentation.** `lib/mapStyle.ts` styles by `layer`, `certainty`, `rank` and
   `geometry_kind` — properties the service computed. MapLibre contains no temporal or historical
   logic (rule 5).
3. **URL is the only view state.** `lib/atlasState.ts` encodes/decodes
   `c,z,t,from,to,cal,mode,l,entity,q,play`. Every view is shareable and the browser back button
   works (ADR-0015).
4. **One API client.** `lib/api.ts` is the only module that knows about `/api/v1`, caching,
   aborts and `ProblemDetails`. Server components use `lib/serverApi.ts`, which shares the types.
5. **Uncertainty is visible.** Dashed outlines for `reconstructed`, hollow markers for
   `uncertain_locus`, explicit notes next to every approximate date, and a coverage panel that
   reports gaps instead of hiding them (ADR-0013).

## Layout

```
src/
├── middleware.ts              locale negotiation (/ → /fa or /en from Accept-Language)
├── app/globals.css            design tokens + every shell class (no UI library)
├── app/[locale]/
│   ├── layout.tsx             the root layout: <html lang dir>, fonts, chrome
│   ├── page.tsx               the atlas (Suspense around AtlasShell)
│   ├── about/  articles/  sources/
│   ├── article/[slug]/        article + automatic entity links + "view on map"
│   └── entity/[type]/[slug]/  entity page: names, time, certainty, relations, disagreements
├── components/
│   ├── AtlasShell.tsx         orchestrator: state ⇄ URL, fetching, selection, playback, keys
│   ├── MapCanvas.tsx          MapLibre host: basemap probe + offline fallback, events only
│   ├── TimelinePanel.tsx      temporal zoom, period bands, histogram, playback, calendars
│   ├── SidePanel.tsx          layers, search, certainty legend, coverage/gaps
│   ├── EntityDrawer.tsx       the map-side entity card
│   └── OpenOnMap.tsx          server-page → atlas deep link
└── lib/
    ├── api.ts  serverApi.ts  types.ts
    ├── atlasState.ts  mapStyle.ts  i18n.ts  markdown.tsx
```

There is deliberately **no** `app/layout.tsx` or `app/page.tsx`: `app/[locale]/layout.tsx` is the
root layout and the middleware owns `/`, so `lang`/`dir` are correct on the very first paint.

## Basemap and offline behaviour

`MapCanvas` probes `https://tiles.openfreemap.io/styles/liberty` (free, keyless) with a 4 s
timeout before constructing the map. If it is unreachable — sandbox, blocked CDN, offline demo —
the map is built on a local fallback style (sand background + one-degree graticule generated from
the study area the API reports), so the historical layers still render and nothing depends on a
third party at runtime. A transient tile error never destroys a working basemap, and a missing
glyph silently hides the label layer instead of spamming the console.

## Keyboard

`←`/`→` move one year (`Shift` = 25), `Space` toggles playback, `Esc` closes the side panel.
List views (`/articles`, `/sources`) exist for low bandwidth and screen readers; the map state is
announced through an `aria-live` region.
