# OhCamel Quant

The real-data quantitative finance platform behind the public site. The top-level [README](../README.md) says
what it does; this file is the map of the code. Operating it: [`docs/runbooks/compute.md`](../docs/runbooks/compute.md).

## Code map

Three layers, and the rule between them: **analytics are pure** (`risk/` .. `fundamentals/`, `models/`,
`kernels/`: no I/O, tested on committed real data and closed forms); **only routers and job handlers read data**
(`api/routers/`, `products/`, `warehouse/ingest/`); every payload and artifact carries `provenance` and honest
`notes`, and nothing substitutes an invented number for missing data.

### Interactive analytics (the API answers these synchronously)

| Path | Contents |
|---|---|
| `src/ohcamel_quant/data/` | `MarketData` facade (warehouse first, then vendors, then committed fixtures), the Parquet + provenance store, providers (Alpaca, Yahoo, Stooq, FRED, Ken French, Cboe, SEC EDGAR, OpenFIGI) |
| `src/ohcamel_quant/risk/` | Nine VaR/ES models (historical, Gaussian, Student-t, Cornish-Fisher, EWMA, GARCH, GJR-GARCH, FHS, EVT-POT), out-of-sample backtests, Euler decomposition, 17 historical and conditional stress scenarios |
| `src/ohcamel_quant/portfolio/` | Covariance estimators, expected returns and Black-Litterman, ten optimizers, walk-forward comparison against 1/N |
| `src/ohcamel_quant/factors/` | Performance statistics, PSR/DSR, Fama-French and custom factor regressions with HAC errors, attribution |
| `src/ohcamel_quant/backtest/` | Look-ahead-safe engine, twelve strategies (ten from the literature, buy-and-hold and a static mix as benchmarks), PBO (CSCV), DSR, SPA, stationary bootstrap, walk-forward, cost curves |
| `src/ohcamel_quant/options/` | BSM/Black-76, IV, parity-implied forwards, SVI and the surface, VIX-style variance, risk-neutral density, realized vol, multi-leg payoffs |
| `src/ohcamel_quant/macro/` | Curve bootstrap, NS/NSS, PCA, bonds and KRDs, recession probit, Sahm rule, Markov regimes, Taylor rules, the 26-series dashboard |
| `src/ohcamel_quant/fundamentals/` | SEC statements, ratios, Piotroski/Altman/Beneish/Sloan/Ohlson, FCFF DCF and reverse DCF |
| `src/ohcamel_quant/deck/` | The Flight Deck: limits (evaluated in the engine's wire shape), the reference books marked to live quotes, the US session clock, the flight recorder (SQLite) |
| `src/ohcamel_quant/api/` | FastAPI app; routers auto-discovered from `api/routers/`; `ratelimit.py` (per-client token bucket on expensive POSTs) |

### Compute tier (the worker runs these on schedules)

| Path | Contents |
|---|---|
| `src/ohcamel_quant/kernels/` | The dispatcher: ten kernels, each with a Rust implementation (`native/kernels`, PyO3 + rayon) and a NumPy reference (`reference.py`) of the same signature. `OHCAMEL_QUANT_KERNELS=rust` (the image) fails at import without the wheel; `=python` forces the reference; unset uses Rust when it imports. `bench.py` validates the benchmark table served at `/api/ops/kernels` |
| `src/ohcamel_quant/jobs/` | `jobs.sqlite` (queue, artifact ledger, scheduler record), the worker (`python -m ohcamel_quant worker`: one job at a time in a child process), admission control (hostd memory, the session window), the cron scheduler and `schedules.yaml`, immutable artifacts with retention, the operator CLI (`python -m ohcamel_quant.jobs status / run-schedule / cancel`) |
| `src/ohcamel_quant/jobs/handlers/` | Handlers for `ops.selftest`, `ingest.fred`, the four public `api.*` kinds (requests over the synchronous caps) and the warehouse ingests |
| `src/ohcamel_quant/warehouse/` | DuckDB (`warehouse.duckdb`, written only by the worker), migrations, read-side helpers in `MarketData`'s shapes, freshness against the session clock, the offline fixture warehouse, committed dated S&P 500 / Nasdaq-100 constituent lists, `python -m ohcamel_quant.warehouse run / build-fixture / freshness` |
| `src/ohcamel_quant/warehouse/ingest/` | Eight ingests: universes, daily bars, minute bars (Alpaca IEX, 50 names), FRED, factors, SEC facts, 13F holdings, option snapshots (Cboe, 30 underlyings) |
| `src/ohcamel_quant/models/` | Pure product analytics: `mc_risk` (P1), `vol_league` (P2), `cov_league` (P3), `farm` (P4), `xsec` + `features` + `cv` (P5, EXP-Q01), `hmm` + `q02` (P6, EXP-Q02), `surface_metrics` (P7), and `verdict` (the charter's gates, applied literally) |
| `src/ohcamel_quant/products/` | The product job handlers, the only Lane M code that reads data: `atlas` (`risk.mc_atlas`, `risk.mc_intraday`), `vol_league`, `cov_league`, `farm`, `q01` (`models.xs_lgbm`, and the universe-freeze CLI), `q02` (`regime.hmm`), `surface_history`; `experiment.py` (frozen configs and the evaluate-once holdout rule), `io.py` (earlier artifacts read back) |

### Elsewhere

| Path | Contents |
|---|---|
| `web/` | React + TypeScript (Vite) in the Paper Tape design system; `web/src/README.md` is the page-author guide |
| `tests/` | pytest, offline against committed real data and a fixture warehouse; `-m live` hits the real vendors |
| `../native/` | Rust: `kernels/` (the PyO3 extension) and `hostd/` (host telemetry daemon) |
| `../research/experiments/EXP-Q01`, `EXP-Q02` | The owner-approved pre-registrations and frozen configs P5 and P6 run under |
| `../tools/perf/` | `bench_kernels.py` (Rust vs Python table), `gate_rt.py` (engine latency gate) |

## Running it

```bash
uv sync --extra dev                  # Python 3.12
make -C .. kernels-dev               # build and install the Rust kernels wheel into .venv (needs rustup)
.venv/bin/pytest -q                  # offline suite (Rust kernels when the wheel imports)
OHCAMEL_QUANT_KERNELS=python .venv/bin/pytest -q    # the same suite on the NumPy references
OHCAMEL_QUANT_LIVE_TESTS=1 .venv/bin/pytest -m live   # real sources (needs internet)
OHCAMEL_QUANT_OFFLINE=1 .venv/bin/python -m ohcamel_quant serve --port 8090
cd web && npm ci && npm run dev      # http://localhost:5173, proxies /api
```

The offline suite's size: the tests `.venv/bin/pytest --collect-only -q`
collects (`-m live` deselected; skips still count, so the number is the same
under `OHCAMEL_QUANT_KERNELS=python`). This line is the source of truth for
every published Quant test count: `scripts/check-counts.sh` reads it and checks
each `<!-- count:quant-tests -->` marker against it, and CI's quant job runs
`scripts/check-counts.sh --verify`, which fails unless it equals pytest's real
collected count. A change to the suite's size updates this line in the same
commit.

<!-- quant-tests -->
```
TESTS = 1304
```
