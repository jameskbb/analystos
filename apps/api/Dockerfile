# AnalystOS API (FastAPI + DuckDB). Build from the repository root:
#   docker build -f apps/api/Dockerfile -t analystos-api .
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# Pango is needed by WeasyPrint for server-side PDF export.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency layer: only lockfile and project metadata, so source edits do not bust the cache.
COPY pyproject.toml uv.lock ./
COPY packages/engine/pyproject.toml packages/engine/pyproject.toml
COPY packages/investigator/pyproject.toml packages/investigator/pyproject.toml
COPY packages/demo-data/pyproject.toml packages/demo-data/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/mcp/pyproject.toml apps/mcp/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages --all-extras --no-install-workspace

COPY packages packages
COPY apps/api apps/api
COPY apps/mcp apps/mcp
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages --all-extras

RUN useradd --system --uid 10001 --home /app analystos \
    && mkdir -p /data && chown analystos /data
USER analystos

# Inside the container the API listens on all interfaces of the container network; publish the
# port on the host's loopback (docker-compose.yml does) or behind a TLS proxy. Production mode
# requires AOS_SECRET_KEY and refuses no-password auth modes unless AOS_ALLOW_INSECURE_LOCAL=true.
ENV PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/data \
    AOS_ENV=production \
    AUTH_MODE=password \
    AOS_HOST=0.0.0.0 \
    AOS_PORT=8000

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# Migrations run at startup (AOS_AUTO_MIGRATE=true); run `analystos-api-migrate` separately to disable that.
CMD ["analystos-api"]
