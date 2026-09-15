# AZ-IR — Interactive Historical Atlas of Iranian Azerbaijan

**اطلس تاریخی تعاملی آذربایجان ایران** · Historical Knowledge Graph · Digital Encyclopedia

A research platform where **map and time are the primary interface**: pick a year, zoom into a
region, and the atlas shows what was there, how certain we are about it, and which source says so.
Coverage begins with Iranian Azerbaijan (East Azerbaijan, West Azerbaijan, Ardabil) and a pilot
corpus around Ardabil and the Safavid rise.

> این یک پایگاه‌دادهٔ مکانی-زمانی است، نه یک نقشهٔ تزئینی: هر عارضه بازهٔ زمانی و دقت زمانی دارد،
> هر گزاره به منبع و شاهد متصل است، و اختلاف‌نظر پژوهشگران حذف یا میانگین گرفته نمی‌شود.

---

## See it running

The default driver needs **no database at all** — the corpus ships as YAML and is normalized in
memory (ADR-0014):

```bash
make install        # python venv + node modules
make dev-backend    # API  → http://localhost:8000  (docs at /docs)
make dev-web        # web  → http://localhost:3000
make check          # lint + types + tests (403 backend tests)
```

Vector tiles — optional until the corpus outgrows GeoJSON (ADR-0018):

```bash
make tiles          # build the archives, one per locale (z0..z10: 1010 tiles, ~850 kB each)
make tiles-verify   # re-read them with `pmtiles`, the reference reader MapLibre uses in the browser
make smoke          # 44 end-to-end contract checks against a running API
```

The map picks the archive up by itself: with one built it serves tiles, without one it keeps serving
the live GeoJSON endpoint. Nothing else changes.

With PostgreSQL + PostGIS (production truth):

```bash
make db-up                                   # docker compose up -d db  (postgis/postgis:16-3.4)
make migrate                                 # alembic upgrade head
make seed                                    # azir seed: fixtures corpus → PostGIS
make dev-users                               # the four editorial logins (AZIR_DEV_PASSWORD)
AZIR_DB_DRIVER=postgis make dev-backend      # same API, real spatial SQL
AZIR_DB_DRIVER=postgis make tiles            # archives rendered from the database
make db-test && make test-postgis            # contract suite against both drivers
```

Every target that can reach the database passes `AZIR_DB_URL` for you (override it with
`make … DB_URL=postgresql+psycopg2://user:pass@host:5432/db`); `AZIR_DB_DRIVER=postgis` without a
URL is refused at startup rather than served as empty data.

`make doctor` prints configuration + data health as JSON and exits non-zero if a lint rule fails
(claims without evidence, published entities without names/geometry, reconstructed extents marked
`exact`). It is wired into CI.

`scripts/smoke.sh` walks the live API end to end (meta → features → timeline → entity → article →
search → context) and fails loudly on any contract violation.

---

## What is implemented (Phase 1)

| Area | Status |
|------|--------|
| Domain core: temporal model, calendars (Gregorian proleptic / Hijri lunar / Solar Hijri / Julian), precision & confidence, semantic zoom ranking, Persian text folding | done |
| Repository port with two adapters: `fixtures` (dev/demo) and `postgis` (production) | done |
| Read-only API v1: `/meta`, `/atlas/{features,timeline,context,query,layers,zoom-levels}`, `/entities/...`, `/articles`, `/sources`, `/search`, `/healthz`, `/readyz` | done |
| Contracts: cursor pagination, RFC 9457 problem details, ETag, field budgets, ≤512 KB payloads, driver disclosure header | done |
| PostGIS schema: 23 tables, frozen migrations, GiST on `geometry` + `int4range` validity, GIN/trgm search indexes, `entity_read_model` view | done |
| SQL search folding (`azir_normalize_fa`) mirroring the Python implementation, asserted by tests | done |
| Frontend: Next.js 15 + MapLibre, RTL Persian primary + English, map ↔ timeline ↔ entity ↔ article | done |
| URL as the single source of view state (shareable, back-button aware) | done |
| Offline fallback basemap with graticule (no third-party tiles required) | done |
| Editorial workflow UI (draft → review → publish), PMTiles delivery, vector tiles | Phase 2–3 |

Test suite: **403 passing** (domain, API contract, fixtures data, schema/migration drift, tile
encoders, CLI) plus **17 PostGIS contract tests** that run in CI against a real
`postgis/postgis:16-3.4` service and compare both drivers on identical queries. CI also rebuilds the
tile archive and re-reads it with the JavaScript PMTiles library the browser uses — over HTTP, range
requests included — so a format mistake fails the build instead of rendering a blank map.

---

## Repository layout

```
AGENTS.md                 22 non-negotiable rules for contributors (human or agent) — read first
docs/
  00-design-review.md     the 10 critical gaps in the original brief and how they were fixed
  01..09-*.md             architecture, domain model, temporal, spatial, API, map/UX, editorial,
                          performance, roadmap
  adr/0001..0018          decisions, superseded rather than edited
backend/
  src/azir/
    domain/               pure logic: temporal, calendar, geo, semantic zoom, text, model, enums
    repositories/
      ports.py            the only thing services may depend on
      fixtures.py         YAML corpus → normalized domain records (also the seeder's normalizer)
      postgis/            schema mirror, mappers, SQL repository, seeder
    services/             atlas, entity, search, editorial, tiles, lint, registry (composition root)
    api/v1/               routers + problem details; no business logic here
    core/                 config, errors, logging, pagination, budgets
    tiles/                PMTiles v3 + MVT v2 encoders (zero new dependencies — ADR-0018)
    cli.py                azir seed | doctor | lint | user | tiles | migrate | serve
  migrations/             frozen Alembic DDL (extensions, tables, indexes, view, folding functions)
  seeds/fixtures/         00..09 YAML: taxonomy, predicates, schemes, periods, sources, places,
                          people, events, polities, links, assertions, articles, geometries
  tests/
frontend/
  src/lib/                api client, URL state codec, map style, tile delivery, i18n, markdown, types
  src/components/         AtlasShell, MapCanvas, TimelinePanel, SidePanel, EntityDrawer
  src/app/[locale]/       fa (RTL, default) + en pages
scripts/                  smoke.sh (HTTP contract), verify-pmtiles.mjs (reference tile reader),
                          ci-report.sh, wsl-setup.sh
docker-compose.yml        postgis + api + web
Makefile                  every routine operation
.github/workflows/ci.yml  lint, types, migrations, seed, doctor, tests (both drivers), tile build +
                          reference-reader verification, web build
```

Layering is enforced by tests and review, not by hope:

```
api/v1/routers  →  services  →  domain (pure, no I/O)
                     ↓
                repositories (port)  →  postgis adapter | fixtures adapter
```

Routers contain no business logic; services never touch SQL; the map never contains historical
logic; the frontend never talks to the database.

---

## Data honesty rules (the part that matters most)

1. **Time is a first-class axis.** Every entity carries a normalized window (`year_from`, `year_to`,
   astronomical years, BCE included) *plus* the precision of the original claim
   (`exact_year`, `circa_year`, `century`, `range`, `before`, `after`, `unknown`) and a confidence.
   An approximate date is never rendered as an exact one.
2. **Claims carry sources.** `assertion` → `evidence` → `source`; a claim without evidence is a lint
   failure, not a publication.
3. **Disagreement is preserved.** Competing positions on one topic are returned together, each with
   its own source, confidence and status (`disputed`), and rendered side by side.
4. **Historical ≠ modern.** `modern_borders` is a separate layer, off by default, never presented as
   a historical boundary.
5. **Certainty drives the visual language.** `reconstructed` extents are dashed and transparent; a
   derived position (`uncertain_locus`, e.g. an event located through its linked place) is hollow
   and labelled as derived.
6. **Gaps are reported.** Events without a location and shapes awaiting real digitisation are
   surfaced in the coverage panel and in `/atlas/features` → `coverage_gaps`.
7. **Nothing is fabricated.** The pilot corpus is built from published scholarship (Cambridge
   History of Iran, Encyclopaedia Iranica, World Heritage dossiers, period sources); every schematic
   geometry is flagged `needs_digitisation` with an explanatory note.

---

## Configuration

Everything is environment-driven (`AZIR_*`, see [`.env.example`](.env.example)). The important ones:

| Variable | Default | Meaning |
|----------|---------|---------|
| `AZIR_DB_DRIVER` | `fixtures` | `fixtures` \| `postgis`; `fixtures` is refused in production |
| `AZIR_DB_URL` | — | required for `postgis`, migrations and seeding |
| `AZIR_FIXTURES_DIR` | `backend/seeds/fixtures` | resolved against CWD or the repo root |
| `AZIR_SUPPORTED_LOCALES` | `["fa","en"]` | `fa` is the default and RTL |
| `AZIR_STUDY_AREA_BBOX` | `[44.0,35.5,49.5,39.8]` | the atlas never invents its own extent |
| `AZIR_MAX_PAYLOAD_KB` | `512` | field budget per response (ADR-0009) |
| `AZIR_BACKEND_URL` | `http://127.0.0.1:8000` | frontend → API proxy target |

---

## Deployment

Target is a PaaS (Vercel/Neon/Supabase or Liara/ArvanCloud), so:

* `backend/Dockerfile` and `frontend/Dockerfile` are standalone, non-root, health-checked images;
  `docker compose up --build` migrates and seeds on API start.
* The frontend proxies `/api/*` to the backend, so only one origin is exposed to browsers.
* Map data is GeoJSON behind the API today and moves to PMTiles over a CDN when the corpus grows
  (ADR-0011) — that changes `lib/mapStyle.ts` and the source, not the state model or components.
* PostGIS must be available on the chosen provider; `make doctor` verifies the extension and the
  applied migration before traffic.

---

## Contributing

Read [`AGENTS.md`](AGENTS.md) first — it is the operating contract (layering, tests with features,
no hard-coded historical data in components, documented decisions). Architectural changes need an
ADR in [`docs/adr/`](docs/adr/); ADRs are superseded, never edited.

Definition of done for any change: `make check` green, new behaviour covered by a test, docs updated
where the contract changed, and no fabricated data anywhere.
