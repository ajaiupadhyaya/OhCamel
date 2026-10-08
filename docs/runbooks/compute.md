# Compute runbook

Operator notes for the quant worker, its jobs and their data. Commands run on the droplet unless a section says
otherwise; the compose services are `ohcamel-quant` (API) and `ohcamel-worker` (jobs), both with `/data` mounted.

## Risk-free rate for `api.portfolio_compare`

`api.portfolio_compare` (and the synchronous `POST /api/portfolio/compare` it backs) needs a daily risk-free
series. `MarketData.risk_free_daily` serves FRED `DGS3MO` (3-month bill), then Ken French's daily `RF`. Nothing
is substituted when both fail.

- **Online (the droplet):** FRED serves `DGS3MO`; nothing to do.
- **Offline (`OHCAMEL_QUANT_OFFLINE=1`, CI, the fixture warehouse):** no `DGS3MO` fixture is committed, because
  no real copy of the series is in the repository and fixtures are never invented, and Ken French data is online
  only. The job fails with `risk-free rate unavailable (...); supply risk_free_rate explicitly`.
- **Fallback:** pass `risk_free_rate` (annual decimal) in the request. The payload reports it as
  `risk_free.source = "user"` and applies it as rate/252 per session. On `/optimize` this is the rail's MANUAL RF.

Tests: `quant/tests/test_jobs_integration.py::test_compare_job_offline_*`.

## EXP-Q01 universe freeze

`models.xs_lgbm` reports `INSUFFICIENT DATA · UNIVERSE NOT FROZEN` until EXP-Q01's universe is frozen. The freeze
applies rule I-Q01-9 to every ETF in `universes.json` against the warehouse, once, after the bars backfill.

The image is read-only, so the freeze cannot edit `research/experiments/EXP-Q01/config.yaml` inside it. With
`--write` it stores the result on the `quant_data` volume, at `/data/experiments/EXP-Q01/universe.yaml`
(`{OHCAMEL_QUANT_DATA_DIR}/experiments/EXP-Q01/universe.yaml`). The job lays that file's `universe` and
`universe_frozen_on` over a `config.yaml` that has none.

On the droplet, from `~/OhCamel` (the deployed sha is checked out):

```bash
OHCAMEL_TAG="$(git rev-parse HEAD)" docker compose -f deploy/docker-compose.yml \
  exec ohcamel-worker python -m ohcamel_quant.products.q01 freeze --write
```

- It reads `/data/warehouse.duckdb` (`OHCAMEL_QUANT_WAREHOUSE_PATH`) read-only, waiting up to 30 s for an
  ingest's write lock. `--warehouse PATH` overrides the path.
- It prints `universe: [...]`, `universe_frozen_on: "..."` and one `# excluded TICKER: reason` line per ETF.
- It runs once. It exits 1 without writing when the universe is already frozen (in the file or in
  `config.yaml`), or when no ETF passes I-Q01-9. The file is created exclusively and is never overwritten.
- Without `--write` it only prints, which is safe to repeat.

The next `models.xs_lgbm` run (monthly, or enqueue it) reads the frozen universe. To make the freeze part of the
repository, paste the two printed lines into `config.yaml` and commit (`quant: freeze EXP-Q01 universe`). The
committed values then take precedence over the file. With the same values `config_hash` does not change, so a
holdout already evaluated stays carried, not stale.

Tests: `quant/tests/test_products_q01.py` (`test_freeze_*`, `test_a_committed_universe_wins_over_the_frozen_file`).
