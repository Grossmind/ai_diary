# Personal AI Voice Diary — production Dockerfile
# Multi-stage: install deps in builder, copy into slim runtime.
# Run as non-root. Data lives on a mounted volume at /data.

# ---- Builder stage: install Python deps into a wheel cache ----
FROM python:3.11-slim AS builder

# Faster installs + reproducible builds
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Install build deps for any wheel that needs compiling
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt


# ---- Runtime stage: slim image with the app + installed deps ----
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    APP_HOST=0.0.0.0 \
    APP_PORT=9000

# Copy installed packages from the builder
COPY --from=builder /install /usr/local

# Create non-root user for runtime
RUN groupadd -r diary && useradd -r -g diary -d /app -s /sbin/nologin diary \
    && mkdir -p /app /data \
    && chown -R diary:diary /app /data

WORKDIR /app
COPY --chown=diary:diary app /app/app

USER diary

EXPOSE 9000

# Quick health check so Container Manager / docker-compose can probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        r=urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=3); \
        sys.exit(0 if r.status==200 else 1)" \
    || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]