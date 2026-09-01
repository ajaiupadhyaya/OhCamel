# syntax=docker/dockerfile:1

# OhCamel, containerised.
#
# Two stages, and the split buys more than image size. The builder carries a
# full opam switch plus the -dev headers for five C libraries -- roughly two
# gigabytes that have no business on a running server, where they are attack
# surface rather than capability. The runtime stage installs only the shared
# objects the binary actually loads and copies one executable in.
#
# The risk in that split is getting the runtime library list wrong. A missing
# shared object does not fail the build; it fails the first time the container
# starts, and it fails as a restart loop rather than a diagnosis. So the
# runtime stage runs ldd at BUILD time and refuses to produce an image if
# anything resolves to "not found" -- see the check near the bottom.

# ---------------------------------------------------------------------------
# Stage 1 -- builder
# ---------------------------------------------------------------------------
FROM ocaml/opam:debian-12-ocaml-5.2 AS builder

USER root

# The C libraries underneath the OCaml dependency tree, and which package
# wants each one:
#
#   libopenblas-dev, liblapacke-dev  owl (via conf-openblas; needs the .pc files)
#   libssl-dev                       async_ssl -- wss:// for Alpaca, https for FRED
#   libffi-dev                       ctypes, which async_ssl reaches OpenSSL through
#   libgmp-dev, m4                   zarith, under mirage-crypto-rng
#   libev-dev                        conf-libev, pulled transitively by the
#                                    websocket stack on some resolutions
#   pkg-config                       how conf-* packages find any of the above
RUN apt-get update && apt-get install -y --no-install-recommends \
      pkg-config \
      m4 \
      libopenblas-dev \
      liblapacke-dev \
      libssl-dev \
      libffi-dev \
      libgmp-dev \
      libev-dev \
      zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

USER opam
WORKDIR /home/opam/ohcamel

# Dependencies before source, deliberately. opam install on this tree takes
# fifteen to twenty minutes; dune build takes about one. Copying only the two
# files that determine the dependency set means editing lib/graph.ml reuses the
# cached opam layer instead of resolving Jane Street's world again.
#
# --deps-only without --with-test: alcotest, qcheck, bisect_ppx and core_bench
# are declared with :with-test in dune-project and are not wanted here. The
# tests run in CI, on a machine that has them.
COPY --chown=opam:opam dune-project ohcamel.opam ./
RUN opam update -y \
 && opam install --deps-only --yes . \
 && opam clean -a -c -s --logs

COPY --chown=opam:opam . .

# The release profile, not the default dev one: no debug assertions, and dune
# stops treating warnings as errors -- which matters because a warning that is
# fatal on the author's compiler can be merely a warning on someone else's, and
# a deployment should not be the place that discovers the difference.
RUN opam exec -- dune build --profile release bin/main.exe \
 && cp _build/default/bin/main.exe /home/opam/ohcamel.exe

# ---------------------------------------------------------------------------
# Stage 2 -- runtime
# ---------------------------------------------------------------------------
FROM debian:12-slim AS runtime

# The runtime halves of the -dev packages above, plus ca-certificates (live
# mode verifies Alpaca's and FRED's certificates and cannot without them) and
# curl (the compose healthcheck calls /api/health; a healthcheck that needs a
# tool the image lacks reports unhealthy forever).
RUN apt-get update && apt-get install -y --no-install-recommends \
      libopenblas0 \
      liblapacke \
      libssl3 \
      libffi8 \
      libgmp10 \
      libev4 \
      zlib1g \
      ca-certificates \
      curl \
 && rm -rf /var/lib/apt/lists/*

# Unprivileged, and with no login shell. The process reads market data and
# serves HTML; nothing it does wants root, and a container escape should land
# somewhere boring.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin ohcamel

COPY --from=builder /home/opam/ohcamel.exe /usr/local/bin/ohcamel

# Fail the BUILD, not the first container start. Without this, a forgotten
# runtime package produces an image that builds cleanly, deploys cleanly, and
# then crash-loops with a linker error at two in the morning.
RUN ldd /usr/local/bin/ohcamel > /tmp/ldd.txt 2>&1 \
 && cat /tmp/ldd.txt \
 && if grep -q "not found" /tmp/ldd.txt; then \
      echo "FATAL: the runtime stage is missing a shared object (above)." >&2; \
      exit 1; \
    fi \
 && rm /tmp/ldd.txt

# Config.default_book_path is the relative string "book.sexp", so the book is
# resolved against the working directory. Baked in as a default; docker-compose
# mounts the repository's copy over it, so editing the book and restarting does
# not require a rebuild.
#
# The source is the EXAMPLE book, not book.sexp. book.sexp is gitignored -- it
# is the owner's private book -- so a fresh clone has no such file, and a COPY
# of it fails the build in its first second, before a line of OCaml compiles.
# The first deploy on a clean droplet found this; the local harness never
# could, because the author's tree always has one.
WORKDIR /app
COPY --chown=ohcamel:ohcamel book.example.sexp /app/book.sexp

USER ohcamel

# Documentation only -- compose publishes nothing from these containers. Caddy
# is the only service with a port on the host.
EXPOSE 8080 8081

ENTRYPOINT ["/usr/local/bin/ohcamel"]
CMD ["demo", "8080"]
