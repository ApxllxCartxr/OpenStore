# The sidecar. One deploy serves one Merchant domain (ADR-0007).
FROM python:3.12-slim AS base

# uv, pinned: the lockfile is the pin and nothing upgrades mid-build (§16.1).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Dependencies first, so a source edit does not re-resolve the world.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
# The token layer the console and approve page are styled from (§4). Without it
# the image serves an unstyled console, which is the kind of thing that only
# shows up once it is deployed.
COPY design/ ./design/
RUN uv sync --frozen --no-dev

# Non-root: the sidecar holds the Merchant's signing key, and a container that
# can rewrite its own code is a container that can rewrite what it signs.
RUN useradd --create-home --uid 10001 sidecar && chown -R sidecar:sidecar /app
USER sidecar

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["uv", "run", "--no-dev", "uvicorn", "openstore.sidecar.app:app", "--host", "0.0.0.0", "--port", "8000"]
