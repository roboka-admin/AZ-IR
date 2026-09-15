# backend — AZ-IR

FastAPI backend for the Interactive Historical Atlas of Iranian Azerbaijan.

Layering (see `../AGENTS.md` and `../docs/01-architecture.md`):

```
api/v1/routers  →  services  →  domain (pure)
                     ↓
                 repositories  →  postgis adapter | fixtures adapter
```

## Run

```bash
make -C .. dev-backend          # fixture driver (no Docker needed)
AZIR_DB_DRIVER=postgis AZIR_DB_URL=postgresql://azir:azir@localhost:5432/azir \
  make -C .. dev-backend        # real PostGIS
```

* `make -C .. check` — lint + types + tests
* `make -C .. migrate` / `make -C .. seed` — Alembic + fixtures into PostgreSQL
* OpenAPI: `http://localhost:8000/docs`

Drivers (ADR-0014): `fixtures` (dev/demo, no DB required) and `postgis` (production truth).
The active driver is always reported in `meta.driver` and `X-AZIR-Driver`.
