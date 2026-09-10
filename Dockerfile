# OpenStore sidecar — production image.
# Non-root, locked deps via uv, migrations run at boot (apply_migrations).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock* README.md REGISTRY.json alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY configs ./configs

RUN uv sync --locked --no-dev

RUN useradd --create-home --uid 10001 app && \
    mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')"

CMD ["uv", "run", "openstore", "serve", "configs/gelateria.yaml"]
