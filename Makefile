# Convenience wrapper.
#
# The opam switch is project-local (./_opam), so the compiler and every
# dependency live inside this directory and are NOT on your PATH by default.
# Each target below re-enters the switch itself, so `make test` works from a
# clean shell with no setup. If you would rather not go through make, run
# `eval $(opam env)` once in the repo and then use dune directly.

OPAM_ENV := eval $$(opam env --switch=$(CURDIR) --set-switch)

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

.PHONY: all build run stress backtest backtest-crisis options test coverage fmt clean deps doctor

all: build

build:
	$(OPAM_ENV) && dune build

run: build
	$(OPAM_ENV) && dune exec bin/main.exe -- synthetic

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
	$(OPAM_ENV) && dune exec bin/main.exe -- live

# Live feeds plus the dashboard on http://localhost:8080. Same credentials as
# run-live.
serve: build
	$(OPAM_ENV) && dune exec bin/main.exe -- serve

# The dashboard driven by a synthetic feed: no credentials, no network, works
# when the market is closed. One symbol is deliberately never ticked, so the
# staleness path is visible rather than theoretical.
demo: build
	$(OPAM_ENV) && dune exec bin/main.exe -- demo

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

# Line coverage, via bisect_ppx.
#
# Instrumentation is off in every other target -- lib/dune declares the backend
# but dune only applies it when asked -- so the build whose tests you normally
# read is not the instrumented one.
#
# Expect a bimodal number, and read it that way rather than as one figure. The
# pure numeric core (graph, attribution, limits, risk_metrics, vol_estimators,
# crisis_data, stress) sits above 85%. The IO edges (the Alpaca websocket, the
# FRED client, the HTTP server, the alert sinks) sit near 40%, because
# exercising them needs a network and every test in this project is hermetic.
# That gap is a design decision showing up in a metric, not a backlog.
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
