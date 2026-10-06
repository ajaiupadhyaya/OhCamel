# syntax=docker/dockerfile:1

# The research service, containerised (Task 16).
#
# One stage: python:3.12-slim plus uv. There is no C-library story here like
# deploy/Dockerfile's -- fdq's own dependencies (numpy, pandas, pyarrow,
# scipy) ship manylinux wheels for the platforms this builds on, so there is
# nothing worth a separate builder/runtime split for image size, and nothing
# a two-stage copy would remove that a single stage's own layers do not
# already discard (uv's cache is cleaned below).
#
# The image's own root becomes REPO_ROOT (ohcamel_research/__init__.py:
# Path(__file__).resolve().parents[3]) by construction: research/ is copied
# to /app/research, so .../research/src/ohcamel_research/__init__.py's third
# parent is /app. Every path a manifest's hashes name (fixtures/history/...,
# fixtures/macro/..., research/config/..., research/experiments/...) and
# every path this Dockerfile copies is repo-root-relative for exactly this
# reason -- there is one directory layout, and it is the repository's own.
# The patch version and its digest, not `3.12-slim`. Two things float in that
# tag, and the second one is the surprise: the interpreter's patch level, and
# the Debian release underneath it -- `-slim` follows Debian stable, so the same
# tag crossed from bookworm to trixie without this file changing. Verified
# 2026-09-20 against registry-1.docker.io: `python:3.12-slim`,
# `python:3.12.14-slim` and `python:3.12.14-slim-trixie` were all the same
# manifest digest, so this pin changed nothing about what is pulled and made
# both moving parts explicit. That matters here because the apt line below
# installs git, ca-certificates and tzdata from whatever release this image is,
# and because the build-time zone check and `uv sync --locked` both run against
# it. Dependabot moves this line (.github/dependabot.yml); a bump that also
# changes the Debian release is a bump to read rather than merge.
FROM python:3.12.14-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS research

# git: uv shells out to it for fdq's git+https dependency (pinned in
# research/pyproject.toml; public, needs no credential -- see the pin's own
# comment there). ca-certificates: TLS to GitHub and PyPI, both reached only
# at build time, never at container run time. tzdata: the schedule is 19:15
# America/New_York (service.py's ZONE, a zoneinfo.ZoneInfo), and the
# lockfile's Python tzdata package never reaches Linux (uv.lock installs it
# only on Windows and Emscripten), so the system zone database is the only
# one the service has -- without it, the service cannot even import. The
# build fails here, as deploy/Dockerfile's does, unless the zone loads: with
# the base image's own interpreter, before any virtualenv exists, so nothing
# but the system database can answer.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      ca-certificates \
      tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && python -c 'import zoneinfo; zoneinfo.ZoneInfo("America/New_York")'

# Every line the service logs (service.py's on_event is print) reaches
# `docker logs` as it is printed. Python block-buffers stdout when it is not
# a terminal, which in a container it never is: a quiet service's lines would
# sit in the buffer for hours, and a crash would lose them outright.
ENV PYTHONUNBUFFERED=1

# uv, pinned to the version research-test's own CI job installs
# (.github/workflows/ci.yml's astral-sh/setup-uv@v5 step) -- so a build here
# and a research-test run in CI resolve the same lockfile the same way.
# Copied from astral's own published image rather than `pip install uv`: no
# extra Python dependency resolution for a tool that is not a dependency of
# ohcamel-research itself, and the binary is reproducible byte-for-byte.
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /usr/local/bin/

WORKDIR /app

# Carried from Task 10's review (binding): the image must be able to judge
# staleness itself. is_stale (manifest.py) reads every hash a committed
# manifest recorded, which reaches into research/ (battery/, config.yaml,
# the friction file, uv.lock) AND fixtures/history/ and fixtures/macro/ --
# neither of which research/ or interface/ alone would carry. All four are
# copied at their repo-relative paths; research.Dockerfile.dockerignore (next
# to this file, and used INSTEAD of the repository's own .dockerignore for
# this build -- see Docker's <dockerfile>.dockerignore lookup) trims what
# actually reaches the image inside them: no .venv/, no uncommitted
# research/experiments/*/results/, and at any depth no .env file, no
# __pycache__ and no *.pyc. The markdown inside these trees does reach the
# image -- only research/README.md is read, by hatchling -- and none of it is
# hashed (see that file's own comments).
COPY research/ research/
COPY interface/ interface/
COPY fixtures/macro/ fixtures/macro/
COPY fixtures/history/ fixtures/history/

WORKDIR /app/research

# --locked refuses to run if research/uv.lock has drifted from
# pyproject.toml, the same guarantee research-test's CI job leans on (see
# its own comment in .github/workflows/ci.yml). No --extra dev: pytest,
# ruff, mypy and the stub packages are for research-test's own machine, not
# this image. uv installs the project itself (ohcamel_research) in editable
# mode as part of a plain `uv sync` of its own root project -- nothing
# further is needed for REPO_ROOT to resolve against /app, above.
RUN uv sync --locked \
 && rm -rf /root/.cache/uv

# Carried from Task 10's review (binding), the other half: a manifest this
# image cannot prove fresh must fail the BUILD, not silently emit
# `unvalidated` forever after every deploy. Every *.json under
# research/experiments/*/manifest.*.json that reached the image (there are
# two today: EXP-A01's) must load and come back with no staleness reasons at
# all -- is_stale(...) == [] -- against exactly the filesystem this image
# just assembled above.
RUN <<'EOF'
uv run --locked python - <<'PYEOF'
import sys
from ohcamel_research import REPO_ROOT
from ohcamel_research.manifest import Manifest, is_stale

manifests = sorted((REPO_ROOT / "research" / "experiments").glob("*/manifest.*.json"))
if not manifests:
    print("no committed manifests found under research/experiments/", file=sys.stderr)
    sys.exit(1)

bad: dict[str, list[str]] = {}
for path in manifests:
    reasons = is_stale(Manifest.load(path), REPO_ROOT)
    if reasons:
        bad[str(path)] = reasons

if bad:
    for path, reasons in bad.items():
        print(f"STALE {path}: {reasons}", file=sys.stderr)
    sys.exit(1)

print(f"{len(manifests)} manifest(s) fresh: " + ", ".join(str(p) for p in manifests))
PYEOF
EOF

# The service's user is uid 10001 -- the engine's (deploy/Dockerfile's
# `useradd --uid 10001 ... ohcamel`), on purpose. The named `signals` volume
# (docker-compose.yml) is mounted read-write here at /signals and read-only
# into ohcamel-live at /data/signals, and Docker seeds an empty named volume
# from the image of whichever container mounts it first, ownership included.
# Compose declares no ordering between the two, so both images carry the
# mount point owned by the SAME uid: whichever one seeds the volume, it comes
# out 10001's, writable by this service and readable by the engine. This
# image used to run as root to sidestep that race -- a guess no build had
# tested; the image job (.github/workflows/image.yml, the signals volume
# step) now runs both orders on fresh volumes and asserts the write and the
# read-only read. No home directory: the root filesystem is read-only in
# compose and the service writes only /signals and /tmp (FDQ_DATA_DIR).
RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin research \
 && mkdir -p /signals \
 && chown 10001 /signals

USER 10001

# The build stamp, last, as deploy/Dockerfile's runtime stage declares it: the
# image job passes both build args, and without an ARG here they were dropped,
# so an operator inside the droplet could not ask which sha the research
# container was. ENV as well as ARG so that `docker run --rm --entrypoint
# printenv <image> OHCAMEL_GIT_SHA` answers (the image job's `stamp: env`
# asserts exactly that); the LABEL because deploy.sh's --build fallback
# passes no --label, so the Dockerfile's own is the only revision an image
# built on the droplet carries. After every RUN, so a new sha re-runs none of
# the uv layers.
ARG OHCAMEL_GIT_SHA=unknown
ARG OHCAMEL_BUILT_AT=unknown
ENV OHCAMEL_GIT_SHA=$OHCAMEL_GIT_SHA
ENV OHCAMEL_BUILT_AT=$OHCAMEL_BUILT_AT
LABEL org.opencontainers.image.revision="$OHCAMEL_GIT_SHA"

ENTRYPOINT ["/app/research/.venv/bin/python", "-m", "ohcamel_research.service"]
