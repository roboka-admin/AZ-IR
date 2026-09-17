# API image. Build context is the repository root:
#   docker build -f backend/Dockerfile .
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libpq only: psycopg2-binary ships its own server bits, and we never compile in the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src

RUN pip install --no-cache-dir .

# Migrations travel with the image so `alembic upgrade head` can run as a release step, and the
# fixture corpus travels with it so a fresh deployment can be seeded (`azir seed`).
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY backend/seeds ./seeds

ENV AZIR_ENV=production \
    AZIR_DB_DRIVER=postgis \
    PORT=8000

EXPOSE 8000
USER nobody

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz',timeout=2)"

CMD ["python", "-m", "uvicorn", "azir.main:app", "--host", "0.0.0.0", "--port", "8000"]
