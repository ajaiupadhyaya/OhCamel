# syntax=docker/dockerfile:1

# OhCamel Quant, containerised: the public site.
#
#   docker build -f quant/Dockerfile -t ohcamel-quant:latest .     # from the REPO ROOT
#
# The build context is the repository root, not quant/, because the offline
# fallback reads the committed real-data fixtures that live outside quant/:
# fixtures/ and docs/crisis/*.csv (data/fixtures.py). What reaches the daemon
# is governed by quant/Dockerfile.dockerignore -- Docker uses a
# <dockerfile>.dockerignore INSTEAD of the root .dockerignore when one exists --
# which is deny-all with an allow-list, so nothing else in the repository (and
# never an .env file) can land in a layer.
#
# The image mirrors the repository's own layout under /app, and that is what
# makes every path in the package resolve with no configuration:
#
#   /app/quant/src/ohcamel_quant/config.py   REPO_ROOT = parents[3] = /app
#   /app/fixtures/                           Settings.fixtures_dir (REPO_ROOT/fixtures)
#   /app/docs/crisis/*.csv                   fixtures_dir.parent/docs/crisis
#   /app/quant/web/dist/                     api/app.py's WEB_DIST (parents[3]/web/dist)
#
# The package is installed EDITABLE (uv's default for the root project), so
# config.py really is at that path; a non-editable install into site-packages
# would move parents[3] somewhere meaningless. The two paths the app writes or
# reads outside the image are still set explicitly below (DATA_DIR,
# FIXTURES_DIR), so a layout change fails loudly rather than silently caching
# into a read-only root.
#
# Three stages; only the last ships:
#   web      node:22-slim  -> quant/web/dist         (~a few MB of static files)
#   pydeps   python + uv   -> /app/quant/.venv       (uv itself stays behind)
#   runtime  python:3.12-slim + venv + src + dist + fixtures, non-root

# ---------------------------------------------------------------------------
# Stage 1 -- the single-page app
# ---------------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /src/quant/web
# playwright is a devDependency for the screenshot script only; its browsers
# are never wanted in a build.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    npm_config_fund=false \
    npm_config_audit=false \
    npm_config_update_notifier=false
# Manifest and lockfile before source: an edit to a .tsx file reuses the
# node_modules layer instead of re-resolving the registry.
COPY quant/web/package.json quant/web/package-lock.json ./
RUN npm ci
COPY quant/web/ ./
RUN npm run build \
 && test -f dist/index.html

# ---------------------------------------------------------------------------
# Stage 2 -- the Python environment, resolved from quant/uv.lock
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS pydeps
# Pinned to the version research.Dockerfile and CI use.
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12
WORKDIR /app/quant
# Dependencies first (the slow layer), without the project, so editing a
# router only redoes the fast second sync. --frozen: install exactly what
# uv.lock says and never re-resolve. The dev extra (pytest, ruff, respx) is an
# optional extra and is not requested; --no-dev also drops any dev group.
COPY quant/pyproject.toml quant/uv.lock quant/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY quant/src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev \
 && .venv/bin/python -c "import ohcamel_quant.config as c; assert str(c.REPO_ROOT) == '/app', c.REPO_ROOT"
# The scientific stack ships its own test suites (pandas alone ~40 MB with
# bytecode); none is imported at run time. Pruned here, in the stage that is
# thrown away, so the runtime layer never contains them. The runtime stage
# imports the whole app afterwards, so a package that did need its tests/
# would fail the build, not a request.
RUN find .venv/lib/python3.12/site-packages \
      \( -path '*/pandas/tests' -o -path '*/numpy/*/tests' -o -path '*/numpy/tests' \
         -o -path '*/scipy/*/tests' -o -path '*/statsmodels/*/tests' \
         -o -path '*/sklearn/*/tests' -o -path '*/sklearn/tests' -o -path '*/pyarrow/tests' \
         -o -path '*/arch/tests' -o -path '*/arch/*/tests' \) \
      -type d -prune -exec rm -rf {} + \
 && rm -rf .venv/lib/python3.12/site-packages/pyarrow/include

# ---------------------------------------------------------------------------
# Stage 3 -- runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# tzdata: the lockfile installs Python's tzdata package only on Windows and
# Emscripten, so zoneinfo (pandas, market-hours logic) needs the system
# database; ca-certificates: every data vendor is HTTPS. python:3.12-slim
# already ships both, so nothing is installed -- but the build asserts they
# are there, so a base-image change fails HERE rather than at the first
# request. The healthcheck uses Python's own urllib, not curl.
RUN python -c 'import zoneinfo; zoneinfo.ZoneInfo("America/New_York")' \
 && test -s /etc/ssl/certs/ca-certificates.crt

# Unprivileged, no login shell. uid 10002 so it never collides with the
# engine image's 10001 on a shared volume.
RUN useradd --system --uid 10002 --create-home --shell /usr/sbin/nologin quant \
 && mkdir -p /data \
 && chown quant:quant /data

WORKDIR /app

# The committed REAL fixtures (Alpaca bars, FRED series, Yahoo crisis closes)
# at their repo-relative paths -- the offline fallback and the test data.
COPY fixtures/ /app/fixtures/
COPY docs/crisis/ /app/docs/crisis/

COPY --from=pydeps /app/quant /app/quant
COPY --from=web /src/quant/web/dist /app/quant/web/dist

# Settings reads APCA_API_KEY_ID / APCA_API_SECRET_KEY (config.py). The
# droplet's /etc/ohcamel/live.env names the same Alpaca data keys the way the
# OCaml engine reads them, ALPACA_API_KEY / ALPACA_SECRET_KEY. This maps the
# latter onto the former when only the latter is present, so one credential
# file serves both services and nothing is duplicated. Values are never
# printed.
RUN cat > /usr/local/bin/ohcamel-quant-entrypoint <<'EOF' && chmod 0755 /usr/local/bin/ohcamel-quant-entrypoint
#!/bin/sh
set -eu
if [ -z "${APCA_API_KEY_ID:-}" ] && [ -n "${ALPACA_API_KEY:-}" ]; then
	APCA_API_KEY_ID="$ALPACA_API_KEY"; export APCA_API_KEY_ID
fi
if [ -z "${APCA_API_SECRET_KEY:-}" ] && [ -n "${ALPACA_SECRET_KEY:-}" ]; then
	APCA_API_SECRET_KEY="$ALPACA_SECRET_KEY"; export APCA_API_SECRET_KEY
fi
exec /app/quant/.venv/bin/ohcamel-quant "$@"
EOF

# /data is the cache (Parquet + provenance sidecars); compose mounts a named
# volume there, which Docker seeds with this directory's ownership on first
# use, so it starts writable by `quant`. /tmp is a tmpfs under compose's
# read-only root; XDG caches point into it so no library tries $HOME.
ENV PATH=/app/quant/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XDG_CACHE_HOME=/tmp/.cache \
    OHCAMEL_QUANT_DATA_DIR=/data \
    OHCAMEL_QUANT_FIXTURES_DIR=/app/fixtures

# Every router imports, against exactly this filesystem -- the pruning above
# and the path layout included. Runs with PYTHONDONTWRITEBYTECODE set, so it
# leaves nothing behind.
RUN python -c "import ohcamel_quant.api.app as a, ohcamel_quant.config as c; \
assert a.WEB_DIST.is_dir(), a.WEB_DIST; assert (c.get_settings().fixtures_dir / 'history').is_dir(); \
print('routes:', len(a.app.routes))"

USER quant
VOLUME ["/data"]
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=4)"]

# Last, so a new sha changes only this metadata layer: smoke.sh compares it
# with the checkout deploy.sh built from.
ARG OHCAMEL_GIT_SHA=unknown
LABEL org.opencontainers.image.title="ohcamel-quant" \
      org.opencontainers.image.source="https://github.com/ajaiupadhyaya/OhCamel" \
      org.opencontainers.image.revision="$OHCAMEL_GIT_SHA"

ENTRYPOINT ["/usr/local/bin/ohcamel-quant-entrypoint"]
CMD ["serve", "--port", "8090"]
