# Convenience wrapper.
#
# The opam switch is project-local (./_opam), so the compiler and every
# dependency live inside this directory and are NOT on your PATH by default.
# Each target below re-enters the switch itself, so `make test` works from a
# clean shell with no setup. If you would rather not go through make, run
# `eval $(opam env)` once in the repo and then use dune directly.

OPAM_ENV := eval $$(opam env --switch=$(CURDIR) --set-switch)

# The build stamp, so a locally built binary says which commit it is. Absent
# a git checkout it says `unknown`, which is the point: an invented date on
# /api/ops would be indistinguishable from a real one.
#
# A fresh OHCAMEL_BUILT_AT on every invocation relinks the binary, because
# dune tracks %{env:...} as a dependency. That costs a couple of seconds and
# buys a built_at that is not a lie. `make test` deliberately does NOT stamp,
# so alternating test and build relinks once each way -- annoying, cheap, and
# preferable to a test suite whose inputs change every second.
BUILD_STAMP := OHCAMEL_GIT_SHA=$$(git rev-parse HEAD 2>/dev/null || echo unknown) \
               OHCAMEL_BUILT_AT=$$(date -u +%FT%TZ)

# Owl workarounds -- only relevant when (re)installing dependencies.
# Two separate problems, two variables. Both are required; owl 1.2 does not
# build on this machine without them.
#
# ---------------------------------------------------------------------------
# (1) OWL_CFLAGS -- works around a compiler crash.
#
# Owl's own build appends `-O3 -march=native` on arm64 macOS. Apple clang 21
# segfaults compiling src/owl/core/owl_ndarray_maths_stub.c at any level above
# -O1:
#
#     clang: error: unable to execute command: Segmentation fault: 11
#     clang: error: clang frontend command failed due to signal
#
# That is a crash in the compiler, not an error in owl, so there is nothing
# upstream to fix. Bisected on this machine against the exact failing command:
#
#     -O3 with -march=native ... SEGFAULT
#     -O3 without            ... SEGFAULT
#     -O2 without            ... SEGFAULT
#     -O1 without            ... COMPILES
#
# OWL_CFLAGS replaces owl's optimisation block wholesale (see the `clean_env_var
# "OWL_CFLAGS"` branch in src/owl/config/configure.ml), so this is owl's own
# flag list with -O3 lowered to -O1 and -march=native dropped. OpenMP is left
# alone -- it compiles fine at -O1 and was never the problem.
#
# COST: owl's C kernels are built at -O1. That is a real hit to Owl's numeric
# hot loops. It does NOT affect BLAS/LAPACK -- those calls land in Homebrew's
# OpenBLAS, which is a separate, fully optimised binary. Since the heavy linear
# algebra here (covariance, VaR) goes through BLAS, the practical impact should
# be small, but it is worth re-measuring if a risk node ever shows up hot.
#
# Revisit when Apple ships a clang that no longer crashes: unset OWL_CFLAGS,
# `make deps`, and see whether owl builds at its own -O3.
export OWL_CFLAGS := -g -O1 -funroll-loops -fno-math-errno -fno-rounding-math -fno-signaling-nans -fexcess-precision=fast -DSFMT_MEXP=19937 -fno-strict-aliasing

# ---------------------------------------------------------------------------
# (2) OWL_LDLIBS -- works around an OpenMP link failure.
#
# Homebrew builds OpenBLAS with USE_OPENMP=1, so its openblas.pc puts
# `-Xpreprocessor -fopenmp` in Cflags. Owl passes those cflags to its C
# compiler, which defines _OPENMP and emits OpenMP outlined functions
# (`*.omp_outlined`, `___kmpc_fork_call`, ...) throughout libowl_stubs.a.
#
# But owl only adds `-lomp` to its link line when OWL_ENABLE_OPENMP=1 -- and it
# is unset, so owl believes OpenMP is off. It compiles OpenMP code it never
# links a runtime for, and the build dies at the final link:
#
#     "___kmpc_fork_call", referenced from:
#         _c_float32_ndarray_get_slice_2 in libowl_stubs.a[66](...)
#     ld: symbol(s) not found for architecture arm64
#
# Two ways out: strip -fopenmp from the openblas cflags, or supply the runtime
# owl forgot to link. Supplying it is chosen here -- it keeps owl's ndarray
# loops parallel instead of silently reverting them to single-threaded, which
# partly offsets the -O1 above.
#
# Note the path override cannot be done via PKG_CONFIG_PATH: conf-openblas
# declares a `setenv` that makes opam *set* (not prepend) that variable to
# Homebrew's directory for every build, so no ordering can win.
export OWL_LDLIBS := -lm -L/opt/homebrew/opt/libomp/lib -lomp

.PHONY: all build run stress backtest backtest-crisis options garch test research-test research-reproduce bench coverage fmt clean deps doctor \
        check-counts \
        quant-deps quant-dev quant-test quant-live-test quant-web quant-serve quant-image \
        deploy-build deploy-up deploy-down deploy-verify deploy-logs deploy-smoke

all: build

build:
	$(OPAM_ENV) && $(BUILD_STAMP) dune build

run: build
	$(OPAM_ENV) && $(BUILD_STAMP) dune exec bin/main.exe -- synthetic

# The scenario suite against the synthetic book: what a chosen move would do to
# exposure, equity, drawdown and every limit. No credentials, no network. Each
# scenario runs on a fork of the engine, so the numbers come out of the same
# nodes that produce the live ones.
stress: build
	$(OPAM_ENV) && dune exec bin/main.exe -- stress

# VaR model validation: is the 95% number this engine reports actually a 95%
# quantile? Kupiec coverage, Christoffersen independence, the joint test and the
# Basel zone, over three deterministic return series chosen so the battery both
# passes and fails in front of you. No credentials, no network.
backtest: build
	$(OPAM_ENV) && dune exec bin/main.exe -- backtest

# The same battery against real market data: the GFC, the COVID crash and the
# 2022 rate shock. Reads adjusted daily closes from docs/crisis/*.csv, which are
# committed, so this needs no credentials and no network either.
#
# If the cache is ever missing, repopulate it with
#
#     python3 tools/fetch_crisis_data.py
#
# and review the diff -- a changed number in a committed cache is a change to a
# published result. This target does NOT fetch, and it does not fall back to the
# synthetic series if the cache is gone: a crisis backtest quietly scoring
# generated data would print a table indistinguishable from the real one.
backtest-crisis: build
	$(OPAM_ENV) && dune exec bin/main.exe -- backtest-crisis

# Greeks-aware exposure: what a delta hedge removes and what it leaves behind,
# against a vol surface that is generated here and labelled synthetic in every
# line it appears in. No credentials, no network.
#
# Live mode ships options risk DISABLED rather than inventing a surface --
# there is no options-chain data source configured, and a fabricated one would
# produce Greeks that looked exactly like real ones.
options: build
	$(OPAM_ENV) && dune exec bin/main.exe -- options

# Why GARCH(1,1) is implemented in lib/vol_estimators.ml and NOT wired into the
# graph. Simulates a known process, fits it back at six sample sizes, and prints
# how far the fit lands from the truth at each. Fixed seed, so the table is the
# same on every machine. About five seconds, no credentials, no network.
#
# This is the unusual case of a mode whose job is to justify an ABSENCE. The
# engine's return window is 60 observations and the persistence -- the whole
# reason to prefer GARCH over EWMA -- comes back biased at that length, not
# merely noisy.
garch: build
	$(OPAM_ENV) && dune exec bin/main.exe -- garch

# Live mode. Needs ALPACA_API_KEY, ALPACA_SECRET_KEY and FRED_API_KEY in the
# environment and a book.sexp (copy book.example.sexp). The engine refuses to
# start if any key is missing rather than degrading to something that looks
# live -- so this target does not try to be clever about locating them:
#
#   set -a; source /path/to/.env; set +a
#   make run-live
#
# NOTE: a free Alpaca plan allows ONE concurrent market-data stream per account.
# If another system is using the same keys, this gets error 406 and stops.
run-live: build
	$(OPAM_ENV) && $(BUILD_STAMP) dune exec bin/main.exe -- live

# Live feeds plus the dashboard on http://localhost:8080. Same credentials as
# run-live.
serve: build
	$(OPAM_ENV) && $(BUILD_STAMP) dune exec bin/main.exe -- serve

# The dashboard driven by a synthetic feed: no credentials, no network, works
# when the market is closed. One symbol is deliberately never ticked, so the
# staleness path is visible rather than theoretical.
demo: build
	$(OPAM_ENV) && $(BUILD_STAMP) dune exec bin/main.exe -- demo

# The example-based suites and the property-based ones run in the same alcotest
# runner, so this is the only test command.
#
# QCHECK_TRIALS raises the number of random cases each property in
# test/test_properties.ml is checked against. The default of 100 keeps `make
# test` under a tenth of a second, which is what a test suite has to cost to
# stay in the loop. 5000 takes under two seconds and is worth running before a
# release; the properties are cheap because none of them touches IO.
#
#   QCHECK_TRIALS=5000 make test
test:
	$(OPAM_ENV) && dune runtest --force

# The research layer's own suite: a separate uv project (research/pyproject.toml),
# not the opam switch above, so this target never touches $(OPAM_ENV).
#
# --locked refuses to run if research/uv.lock is out of date with
# pyproject.toml, rather than silently rewriting it -- so CI fails loudly on a
# drifted lockfile instead of quietly resolving a different one than what was
# committed.
#
# The two `fixtures doctor` runs are the same check `tests/test_fixtures_doctor.py`
# makes pytest enforce, run again here as a standalone command so a broken or
# missing fixture (bad sidecar, wrong window, no provenance) fails `make
# research-test` loudly on its own line, not only inside the test process.
# `macro.parquet` gets its own directory and its own doctor run rather than
# living beside the nine ETFs, per fixtures/history/README.md.
research-test:
	cd research && uv sync --locked --extra dev && uv run pytest && uv run ruff check
	cd research && uv run ohcamel-research fixtures doctor --fixtures $(CURDIR)/fixtures/history
	cd research && uv run ohcamel-research fixtures doctor --fixtures $(CURDIR)/fixtures/macro

# Every committed battery manifest, re-derived: `ohcamel-research battery run`
# for every experiment, on a temporary copy of HEAD's research/, fixtures/ and
# interface/ (never this checkout), compared with what was committed once
# ran_at is masked -- see tools/reproduce_manifests.py for the comparison and
# why python_version and a float's last digits may differ off the machine the
# evidence was run on. is_stale proves a manifest names the bytes it hashed;
# this proves its numbers are what those bytes produce. Offline and hermetic:
# no credential, no network, committed fixtures only. About 26 seconds an
# experiment. CI's research job runs it after research-test.
research-reproduce:
	cd research && uv run --locked --offline --extra dev python $(CURDIR)/tools/reproduce_manifests.py

# The three published test counts, checked against their source of truth:
# lib/verified.ml's `tests` and `scheduler_tests`, and
# research/src/ohcamel_research/verified.py's `TESTS`. Deliberately NOT
# $(OPAM_ENV): scripts/check-counts.sh is grep and sed only, so this target
# runs on a plain runner with no opam switch at all -- which is exactly the
# `lint` job in CI that gates every push on it.
check-counts:
	@scripts/check-counts.sh

# What a tick costs, in seconds and in words, against a throwaway
# poll-and-recompute baseline. `make run` counts NODES; this counts time and
# allocation, which is the number a latency-conscious reader actually wants.
#
# Takes a minute or two. NOT run by `make test` and NOT gated in CI --
# benchmark numbers from a shared runner are noise. The README quotes a local
# run and names the hardware.
#
# core_bench's own flags can be passed through:
#   dune exec bench/bench_graph.exe -- -quota 10
bench:
	$(OPAM_ENV) && dune exec bench/bench_graph.exe

# Line coverage, via bisect_ppx.
#
# Instrumentation is off in every other target -- lib/dune and desk/dune
# declare the backend but dune only applies it when asked -- so the build
# whose tests you normally read is not the instrumented one.
#
# Expect a bimodal number, and read it that way rather than as one figure. The
# pure numeric core (graph, attribution, limits, risk_metrics, vol_estimators,
# crisis_data, stress, and the risk-depth modules: long_panel, factor_model,
# liquidity) sits from 83% up. The IO edges that actually reach a network --
# the Alpaca websocket, the Alpaca REST client, the FRED client, the alert
# sinks' Slack post, the desk's own Alpaca transport, and the venue's
# trade-updates socket -- sit from 36% to 65%, because exercising them needs a
# network and every test in this project is hermetic. That gap is a design
# decision showing up in a metric, not a backlog.
coverage: build
	@rm -rf _coverage && mkdir -p _coverage
	$(OPAM_ENV) && BISECT_FILE=$(CURDIR)/_coverage/ohcamel 	  dune runtest --force --instrument-with bisect_ppx
	$(OPAM_ENV) && bisect-ppx-report html --coverage-path _coverage -o _coverage/html
	$(OPAM_ENV) && bisect-ppx-report summary --per-file --coverage-path _coverage
	@echo
	@echo "  HTML report: _coverage/html/index.html"

# Reformat in place with ocamlformat (config in .ocamlformat).
fmt:
	$(OPAM_ENV) && dune fmt

clean:
	$(OPAM_ENV) && dune clean
	rm -rf _coverage

# Re-install dependencies from dune-project into the local switch.
deps:
	$(OPAM_ENV) && opam install --deps-only --with-test -y .

# Print what is actually installed. Worth running before believing a build
# failure is your code -- on macOS the usual culprit is Owl.
doctor:
	@$(OPAM_ENV) && echo "ocaml:    $$(ocaml -version)" \
	  && echo "dune:     $$(dune --version)" \
	  && echo "switch:   $$(opam switch show)" \
	  && echo "clang:    $$(/usr/bin/cc --version | head -1)" \
	  && echo "openblas: $$(pkg-config --modversion openblas 2>/dev/null || echo 'NOT FOUND')" \
	  && echo "--- key packages ---" \
	  && opam list --installed --columns=name,version core async incremental owl cohttp-async alcotest

# ---------------------------------------------------------------------------
# OhCamel Quant (quant/): the public site -- FastAPI + React, real data only
# ---------------------------------------------------------------------------
#
# Its own uv project (quant/pyproject.toml, quant/uv.lock) and npm project
# (quant/web), like research/: none of these targets touches $(OPAM_ENV).
# --frozen everywhere: install exactly what the lockfile says, never
# re-resolve -- the same guarantee the Docker image and CI rely on.

QUANT_PORT ?= 8090

# Python deps (with the dev extra: pytest, ruff, respx) and the web app's
# node_modules. npm ci only when node_modules is missing or older than the
# lockfile, so repeated `make quant-dev` stays fast.
quant-deps:
	cd quant && uv sync --frozen --extra dev
	@if [ ! -d quant/web/node_modules ] || [ quant/web/package-lock.json -nt quant/web/node_modules ]; then \
	  cd quant/web && npm ci; \
	fi

# The API on :$(QUANT_PORT) and Vite on :5173 (which proxies /api to it),
# together; Ctrl-C stops both. OFFLINE by default: committed real fixtures
# only, no network, no keys -- set OHCAMEL_QUANT_OFFLINE=0 to fetch live data.
quant-dev: quant-deps
	@echo "  web  http://localhost:5173    api  http://localhost:$(QUANT_PORT)/api/docs"
	@trap 'kill 0' INT TERM EXIT; \
	  (cd quant && OHCAMEL_QUANT_OFFLINE=$${OHCAMEL_QUANT_OFFLINE:-1} \
	     uv run --frozen ohcamel-quant serve --host 127.0.0.1 --port $(QUANT_PORT)) & \
	  (cd quant/web && npm run dev) & \
	  wait

# Lint and the offline suite (committed real fixtures; conftest.py forces
# OHCAMEL_QUANT_OFFLINE=1). What CI's `quant` job runs.
quant-test:
	cd quant && uv sync --frozen --extra dev && uv run --frozen ruff check && uv run --frozen pytest -q

# The tests marked `live`: they hit the real vendors (Alpaca/Yahoo/Stooq,
# FRED, Cboe, SEC, Ken French). Needs network; vendors rate-limit.
quant-live-test:
	cd quant && OHCAMEL_QUANT_LIVE_TESTS=1 uv run --frozen pytest -m live -q -rA

# Typecheck and build the SPA into quant/web/dist, which the API serves at /.
quant-web:
	cd quant/web && npm ci && npm run typecheck && npm run build

# The built app, as production runs it, on http://localhost:$(QUANT_PORT)
# (online: real vendors, cached under quant/.data).
quant-serve: quant-web
	cd quant && uv sync --frozen && uv run --frozen ohcamel-quant serve --port $(QUANT_PORT)

# The production image, from the repository root (it bakes in fixtures/ and
# docs/crisis/). deploy.sh builds it through compose on the droplet.
quant-image:
	docker build -f quant/Dockerfile \
	  --build-arg OHCAMEL_GIT_SHA=$$(git rev-parse HEAD 2>/dev/null || echo unknown) \
	  -t ohcamel-quant:latest .

# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------
#
# These targets drive the LOCAL harness -- the public Quant app on
# http://localhost:8000 and the containerised synthetic engine on
# http://localhost:8001 (--profile demo; it is no longer public anywhere),
# behind the same Caddy configuration production uses, without TLS. They exist
# so the deployment can be broken and fixed on a laptop instead of on a
# droplet.
#
# Deploying for real is deploy/deploy.sh, run on the droplet. There is no
# `make deploy` on purpose: a target that silently reaches a production host
# is a target somebody eventually runs by accident.

LOCAL_COMPOSE := docker compose --env-file deploy/local.env \
                  -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml \
                  --profile demo

# Build both images. The engine: twenty minutes cold, about one after an edit
# to lib/, because the Dockerfile installs dependencies before it copies
# source. Quant: a few minutes cold, seconds after an edit.
deploy-build: quant-image
	docker build -f deploy/Dockerfile \
	  --build-arg OHCAMEL_GIT_SHA=$$(git rev-parse HEAD 2>/dev/null || echo unknown) \
	  --build-arg OHCAMEL_BUILT_AT=$$(date -u +%FT%TZ) \
	  -t ohcamel:latest .

# Quant on http://localhost:8000, the synthetic engine on :8001, both behind Caddy.
deploy-up: deploy-build
	$(LOCAL_COMPOSE) up -d --no-build
	@echo
	@echo "  quant      http://localhost:8000"
	@echo "  engine     http://localhost:8001   (synthetic demo, harness only)"
	@echo "  verify     make deploy-verify"

deploy-down:
	$(LOCAL_COMPOSE) down

deploy-logs:
	$(LOCAL_COMPOSE) logs -f --tail 100

# The assertion the whole deployment turns on: frames must arrive SPREAD OVER
# the window, not delivered in a pile at the end. See the comment above the
# stream check in deploy/smoke.sh for why counting frames is not enough.
deploy-smoke:
	deploy/smoke.sh http://localhost:8000 --engine http://localhost:8001

# Up, verify, down. What CI would run if this were wired into CI.
deploy-verify: deploy-up
	@sleep 5
	@deploy/smoke.sh http://localhost:8000 --engine http://localhost:8001; status=$$?; \
	  $(LOCAL_COMPOSE) down >/dev/null 2>&1; \
	  exit $$status
