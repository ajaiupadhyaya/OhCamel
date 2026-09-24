# OhCamel Quant

The real-data quantitative finance platform behind the public site. The
top-level [README](../README.md) describes what it does; this file is the map
of the code.

| Path | Contents |
|---|---|
| `src/ohcamel_quant/data/` | `MarketData` facade, Parquet + provenance store, providers (Alpaca, Yahoo, Stooq, FRED, Ken French, Cboe, SEC EDGAR, OpenFIGI), committed-fixture loader |
| `src/ohcamel_quant/risk/` | VaR/ES models, GARCH/GJR/FHS/EVT, out-of-sample backtests, Euler decomposition, historical & conditional stress |
| `src/ohcamel_quant/portfolio/` | Covariance estimators, expected returns & Black–Litterman, optimizers, walk-forward comparison |
| `src/ohcamel_quant/factors/` | Performance statistics, PSR/DSR, Fama–French & custom factor regressions, attribution |
| `src/ohcamel_quant/backtest/` | Look-ahead-safe engine, literature strategies, PBO/DSR/SPA/bootstrap/walk-forward |
| `src/ohcamel_quant/options/` | BSM/Black-76, IV, parity-implied forwards, SVI, surface, VIX-style variance, risk-neutral density, realized vol, strategies |
| `src/ohcamel_quant/macro/` | Curve bootstrap, NS/NSS, PCA, bonds & KRDs, recession probit, Sahm rule, Markov regimes, Taylor rules, dashboard |
| `src/ohcamel_quant/fundamentals/` | SEC statements, ratios, Piotroski/Altman/Beneish/Sloan/Ohlson, FCFF DCF & reverse DCF |
| `src/ohcamel_quant/api/` | FastAPI app; routers are auto-discovered from `api/routers/` |
| `web/` | React + TypeScript app (Vite); `web/src/README.md` is the page-author guide |
| `tests/` | pytest, offline against committed real data; `-m live` hits the real vendors |

Rules the code follows: analytics are pure functions (no I/O); only routers
touch `MarketData`; every payload carries `provenance` and honest `notes`;
nothing substitutes an invented number for missing data.

```bash
uv sync --extra dev                  # Python 3.12
.venv/bin/pytest -q                  # offline suite
OHCAMEL_QUANT_LIVE_TESTS=1 .venv/bin/pytest -m live   # real sources (needs internet)
OHCAMEL_QUANT_OFFLINE=1 .venv/bin/python -m ohcamel_quant serve --port 8090
cd web && npm ci && npm run dev      # http://localhost:5173, proxies /api
```
