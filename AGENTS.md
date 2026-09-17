# AGENTS.md — Operating rules for AI agents & contributors

**Project:** Interactive Historical Atlas of Iranian Azerbaijan (`AZ-IR`)
**Product definition:** Interactive Historical Atlas + Historical Knowledge Graph + Digital Encyclopedia.
**Center of the experience:** *Map + Time*. The map is the primary interface for discovering the historical database.

Read before writing any code: `docs/00-design-review.md` and `docs/adr/`.

---

## 1. Non-negotiable rules

These are enforced in review and, where possible, in CI/lint.

1. **Frontend must never access the database directly.** No SQL, no connection strings, no ORM in `frontend/`.
2. **API routers must not contain business logic.** Routers do: parse → validate → call a service → shape the response. Nothing else.
3. **Business logic belongs in the service/domain layers.** `domain/` is pure (no I/O, no framework imports) and unit-testable.
4. **Database access belongs behind repositories.** Only `repositories/` may import SQLAlchemy / write SQL.
5. **MapLibre must never contain historical business logic.** No `if (year > 1500)` inside map code. The backend decides what existed when; the map renders it.
6. **Historical data must support temporal uncertainty.** Never coerce `circa 1400`, `8th century`, or `867 AH` into a bare integer year. See ADR-0005.
7. **Historical claims must support source/evidence references.** No evidence → not publishable. See ADR-0003/0010.
8. **Articles must link to entities instead of duplicating entity data.** An article is a *view* over data, never the source of truth for a fact.
9. **Modern administrative boundaries must never be treated as historical boundaries.** They are a separate layer with a separate `kind` and their own source.
10. **Never fabricate historical data.** Not in fixtures, not in seeds, not in tests, not in demos. Sample/demo data must be marked (`status=draft|sample`, `meta.driver=fixtures`) and must never appear as published truth.
11. **Spatial data belongs in PostGIS.** Geometries are stored and queried with `geometry` columns, GiST indexes and `ST_*` functions. Do not re-implement bbox/intersection math in application code except inside the documented `fixtures` dev driver (ADR-0014).
12. **Historical data and presentation/UI data stay conceptually separate.** `importance`, `rank`, `min_zoom`, `label_*`, `color_*` are derived presentation fields; they must never overwrite or replace historical attributes.
13. **API contracts must be versionable.** Everything public lives under `/api/v1`. A breaking change means `/api/v2` plus a documented migration; it never means silently changing `v1`.
14. **Features must include tests.** No test → not done. Contract tests run against **both** drivers (`postgis`, `fixtures`).
15. **Avoid unnecessary dependencies.** Every new dependency needs a one-line justification in the PR/commit and, if architectural, an ADR.
16. **No major architectural change without an ADR** explaining why (`docs/adr/NNNN-*.md`, ADRs are superseded, never edited).
17. **Do not hard-code historical data into frontend components.** If a string is a historical fact, it comes from the API. UI copy lives in `messages/*.json`.
18. **The map is a presentation/query interface, not the source of truth.**
19. **Preserve the separation** between historical research data, application logic and presentation.
20. **Disagreement is data, not noise.** Competing historical claims are stored as separate assertions with `status=disputed` and both are surfaced in the API. Never resolve a dispute by deleting one side.
21. **Every entity write must produce an `audit_log` entry** and bump `revision`.
22. **Persian is the primary content language; the UI is RTL-first.** Never build a layout that only works LTR. Latin/English is a first-class secondary locale, not a translation afterthought bolted on.

---

## 2. Repository layout

```
AZ-IR/
├── AGENTS.md                  ← you are here
├── docs/                      ← design review, architecture, contracts, ADRs
├── backend/                   ← FastAPI (Python 3.11+), layered
│   ├── src/azir/
│   │   ├── core/              config, logging, errors, ids, pagination, i18n, db session
│   │   ├── domain/            pure domain: temporal, calendar, semantic zoom, ranking, text
│   │   ├── repositories/      data access (postgis adapter + fixtures adapter)
│   │   ├── services/          application layer (atlas, entity, article, timeline, search)
│   │   ├── api/v1/            routers only
│   │   └── schemas/           pydantic API contracts
│   ├── migrations/            Alembic
│   ├── seeds/                 YAML fixtures (single source of seed data for both drivers)
│   └── tests/
├── frontend/                  ← Next.js + TypeScript + MapLibre GL JS
├── infra/                     ← docker-compose, nginx, tile pipeline
└── .github/workflows/ci.yml
```

### Backend layering (dependency direction is one-way)

```
api/v1/routers  →  services  →  domain (pure)
                     ↓
                 repositories  →  SQLAlchemy/PostGIS  →  PostgreSQL
```

- `domain/` imports nothing from `services/`, `repositories/`, `api/`, FastAPI or SQLAlchemy.
- `repositories/` never import `services/`.
- `services/` never build SQL strings and never import FastAPI.
- `api/` never import `repositories/` directly (always through a service).
- Cross-module data access goes through that module's repository/service, never another module's tables.

---

## 3. Definition of Done

A task is done only when **all** of these exist:

- [ ] Code in the correct layer, with the correct dependency direction.
- [ ] Pydantic schema for any new API contract; OpenAPI regenerates cleanly.
- [ ] Unit tests for domain logic; contract/integration tests for API.
- [ ] Migration (forward **and** verified reversible where destructive) if schema changed.
- [ ] Fixture/seed data updated if a new entity type or layer was added.
- [ ] `ruff check`, `ruff format --check`, `mypy`, `eslint`, `tsc --noEmit` all pass.
- [ ] Docs updated: the relevant `docs/*.md`, and an ADR if a decision was made.
- [ ] i18n keys added for **both** `fa` and `en` (no hard-coded UI strings).
- [ ] No fabricated history: every seeded fact has a real source reference or is explicitly marked sample/draft.
- [ ] Accessible: keyboard reachable, `aria-*` present, works in RTL.

---

## 4. Conventions

### Python
- Python 3.11+, `ruff` (lint+format, line-length 100), `mypy --strict` on `src/azir`.
- SQLAlchemy 2.0 typed style (`Mapped[...]`, `mapped_column(...)`), no legacy `Session.query`.
- Pydantic v2, `from_attributes=True` for ORM → schema mapping.
- Settings via `pydantic-settings` and env vars only (`AZIR_*`). No config files read at runtime.
- Structured JSON logging; every request gets a `request_id`. Never log secrets or full user payloads.
- Errors: raise domain exceptions → mapped to RFC 9457 Problem Details by a handler. Never return raw 500s with stack traces.
- Naming: tables `snake_case` singular-ish (`place`, `place_link`), enums as Postgres types, ids `<prefix>_<uuidv7>` in API payloads.

### TypeScript / Frontend
- `strict: true`, ESLint + Prettier, no `any` (use `unknown` + narrowing).
- Server Components by default; `"use client"` only where interaction demands it.
- All data through `lib/api.ts` (typed client for `/api/v1`). Relative URLs only — the dev server proxies `/api` to the backend so the browser never targets `localhost`.
- Map state (center, zoom, time window, layers, selected entity) is **URL-synced** → every view is shareable/deep-linkable.
- Styling: design tokens in CSS custom properties (`styles/tokens.css`), RTL-first logical properties (`margin-inline-start`, not `margin-left`).
- Fonts are self-hosted (Vazirmatn). No third-party font/CDN dependency that can break for users inside Iran.

### Data / fixtures
- Seeds live in `backend/seeds/fixtures/*.yaml` and are the **only** source of seed data (used by the Postgres seeder *and* the fixtures driver).
- Every fixture fact carries `sources: [...]` pointing to a real bibliographic record.
- Fixture validation runs in CI (`make lint-data`): death > birth, geometry inside the study area bbox, every accepted assertion has evidence, every published entity has ≥1 source, controlled vocabularies only.

### Git
- Branch from `main`; PR title = conventional commit (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`).
- One logical change per commit; migrations never bundled with unrelated refactors.
- Never commit `.env`, credentials, or generated artifacts (`dist/`, `*.pmtiles`, `node_modules/`).

---

## 5. Domain invariants (must hold in tests and in CI)

1. `normalized.year_from <= normalized.year_to` always.
2. An entity's projection columns (e.g. `person.birth_place_id`) must be derivable from `accepted` assertions; if two accepted assertions conflict, the projection is empty and the entity exposes `disagreements`.
3. `place_link` must be acyclic for `contains`/`part_of`.
4. A `published` entity has ≥1 source; an `accepted` assertion has ≥1 evidence with `stance=supports`.
5. Modern administrative geometry (`kind=modern_admin`) never appears in a layer whose `kind` is historical.
6. Any feature returned by `/atlas/features` must satisfy `min_zoom <= requested zoom` and intersect the requested `bbox`/time window per the requested `mode`.
7. No hard deletes of historical entities; `archived` + tombstone only.

---

## 6. Sensitive-subject rules

- Ethnicity, religion, language, and identity of persons/places are **reported claims with sources**, never asserted facts. They are stored as assertions, not as entity columns.
- Contemporary geopolitical claims are out of scope. The project covers history.
- Borders, contested territories and national narratives: show the sources, mark uncertainty visually, keep the permanent disclaimer (ADR-0013).
- Never use historical data to make a modern political claim, and never "balance" a claim by inventing a counterpart.

---

## 7. How to work in this repo (agent workflow)

1. Read the relevant ADR **before** implementing. If your change contradicts an ADR, write a superseding ADR first.
2. Prefer the smallest vertical slice that a user can see working (walking skeleton) over broad half-finished layers.
3. When touching temporal or spatial logic, add a test with a real historical example (e.g. Shah Ismail, born in Ardabil, 892 AH / 1487 CE) rather than an abstract one.
4. If data is missing, **do not invent it** — return fewer results and mark coverage gaps (`meta.coverage`).
5. Run `make check` (lint + types + tests) before declaring done.
