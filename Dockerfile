# Multi-Stage-Build für die FastAPI-App.
#
# Stage 1 (builder): Python-Dependencies in ein virtuelles Env installieren.
# Stage 2 (runtime): nur das Env + den App-Code kopieren — kleines Image.
#
# Warum Multi-Stage:
#   * gcc/g++ braucht's für psycopg + pgvector beim Build, im Laufzeit-Image
#     wären sie unnötiger Ballast (~150 MB).
#   * Layer-Caching: solange pyproject.toml unverändert ist, wird der
#     pip-Layer wiederverwendet — Code-Änderungen führen nicht zu Re-Install.
#
# Sicherheit:
#   * Non-root-User (``app``) läuft die API.
#   * Keine Build-Tools im Final-Image.
#   * .env wird NICHT ins Image kopiert; Secrets kommen via env-Variablen
#     aus docker-compose oder dem Orchestrator (k8s-Secret, ECS-TaskDef, ...).

# ---------------------------------------------------------------------------
# Stage 1: Builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build-Dependencies für native Wheels (psycopg-binary brauchts streng
# genommen nicht, aber pgvector + sentence-transformers könnten später
# dazukommen).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Erst nur die Manifest-Datei — sorgt für Layer-Cache, solange Deps stabil.
COPY pyproject.toml ./

# Dummy-Source-Layout, damit pip install -e nicht auf nicht-existente
# Pakete schimpft. Wird in Stage 2 durch echten Code überschrieben.
RUN mkdir -p src/methods && touch src/methods/__init__.py

# Build-Venv anlegen und alle Extras installieren, die der Container braucht:
# api (FastAPI/Uvicorn), embed (OpenAI-SDK fürs Live-Querying). Kein dev.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[api,embed]" gunicorn

# ---------------------------------------------------------------------------
# Stage 2: Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    # Storage-Mode: "snapshot" = liest aus data/snapshot/ (Cloud-Default,
    # keine DB-Verbindung). "postgres" = klassischer Pfad mit Alembic + ORM.
    # In Sliplane/Render/Fly überschreiben, falls Postgres-Container daneben läuft.
    STORAGE=snapshot \
    SNAPSHOT_DIR=/app/data/snapshot

# Nur die Runtime-Lib für Postgres — kein build-essential, kein gcc.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app -g 1000 \
    && useradd -r -g app -u 1000 -d /app -s /bin/bash app

# Venv aus Builder rüberkopieren.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# App-Code + Skripte, die zur Laufzeit gebraucht werden.
# bootstrap.sh + embed_methods.py kommen mit ins Image (siehe .dockerignore).
# data/ wird mit-gebacken, damit der Container ohne extra-Mount auskommt
# (Methoden-YAMLs sind statischer Content der App).
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY data/ ./data/
COPY scripts/ ./scripts/
COPY browse/app/ ./browse/app/

# Berechtigungen vor User-Switch — Root muss den Code besitzen.
RUN chown -R app:app /app

USER app

EXPOSE 8000

# Healthcheck nutzt unseren /health-Endpoint, der DB-Reachability prüft.
# 30s Start-Period, weil Migration + Embed-Lookup beim Boot etwas dauert.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

# Gunicorn statt uvicorn-Standalone — mehrere Worker für echte Last,
# Uvicorn-Worker für ASGI. Port + Worker-Anzahl überschreibbar via env:
#   PORT             — Port zum Lauschen (PaaS-Standard, z.B. Sliplane/Heroku/Render).
#                       Default 8000.
#   WEB_CONCURRENCY  — Anzahl Worker. Default 4.
# Shell-Form, damit ${PORT:-8000} expandiert wird.
CMD gunicorn methods.api:app \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-4}" \
    --worker-class uvicorn.workers.UvicornWorker \
    --access-logfile - \
    --error-logfile - \
    --timeout 60
