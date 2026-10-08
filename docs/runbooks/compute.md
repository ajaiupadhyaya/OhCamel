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
