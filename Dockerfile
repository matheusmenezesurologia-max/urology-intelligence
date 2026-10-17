# ---------------------------------------------------------------------------
# Urology Intelligence — Application image
# Multi-stage build for a slim, production-ready Python 3.12 runtime.
# ---------------------------------------------------------------------------

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=America/Sao_Paulo

# System libraries:
#   - build-essential / libpq-dev : psycopg2 compilation
#   - WeasyPrint native deps       : libpango, libcairo, libgdk-pixbuf, libffi
#   - PyMuPDF runtime              : libjpeg, zlib
#   - curl                         : container HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        libcairo2 \
        libjpeg62-turbo \
        shared-mime-info \
        fonts-liberation \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Dependency layer (cached unless requirements.txt changes) ---
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# --- Application source ---
COPY . .

# Non-root runtime user
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/storage/pdfs /app/storage/newsletters /app/storage/cache /app/storage/clinical_knowledge \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Default: launch the API + scheduler supervisor.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
