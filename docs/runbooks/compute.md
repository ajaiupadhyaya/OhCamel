# Compute runbook

*As of 2026-10-08, against `ship/harden`.* Operator notes for the quant worker, its jobs and their data. Commands
run on the droplet as the deploy user, from `~/OhCamel` (the deployed sha is checked out there), unless a section
says otherwise. The compose services are `ohcamel-quant` (API), `ohcamel-worker` (jobs) and `ohcamel-hostd` (host
telemetry); the first two mount the `quant_data` volume at `/data`.

Every command below uses this helper (compose needs `OHCAMEL_TAG`, which `deploy.sh` sets per command and never
exports):

```bash
dc() { OHCAMEL_TAG="$(git -C ~/OhCamel rev-parse HEAD)" docker compose -f ~/OhCamel/deploy/docker-compose.yml "$@"; }
```

What lives where, inside `/data`:

| Path | Written by | Backed up |
|---|---|---|
| `/data/jobs.sqlite` (WAL) | worker and API (`POST /api/jobs`) | nightly, `deploy/backup.list` (`sqlite`), 14 kept |
| `/data/artifacts/<kind>/<id>/` | worker only, immutable once published | no |
| `/data/warehouse.duckdb` | worker's `ingest.*` jobs only; the API opens it read-only | no (`duckdb` method reserved, refused by `backup.sh`) |
| `/data/experiments/EXP-Q01/universe.yaml` | the Q01 freeze, once | no |
| `/data/deck/recorder.sqlite` | API (the Flight Deck recorder) | nightly (`sqlite`) |

Backups go to `/var/backups/ohcamel`. `deploy/backup.sh` refuses to run while that directory is missing, and on
2026-10-08 it still is: the owner's root step `install -d -m 0770 -o 10001 -g ohcamel /var/backups/ohcamel`
(finish plan O12) is outstanding. Until then no copy of `jobs.sqlite` exists, and "restoring jobs.sqlite" below
can only start from an empty queue.

## Reading hostd

`ohcamel-hostd` samples `/proc` and the three slices every 5 s and keeps 720 samples (one hour). The API proxies
it at `GET /api/ops/host`, which is public; `/compute` draws it.

```bash
curl -s https://ohcamel.ajaiupadhyaya.com/api/ops/host | jq '.latest | {cpu, steal, iowait, load1,
  mem_available_mib: (.mem_available / 1048576 | floor), swap_used_mib: (.swap_used / 1048576 | floor), groups}'
```

- `cpu`, `steal`, `iowait` are fractions of all CPUs (2 on `s-2vcpu-4gb`) over the last interval. A slice's
  `cpu` is in cores, its `mem` in bytes. A slice that could not be read is `null`, never 0, and `notes` names it.
- `latest: null` means hostd has not finished its first interval (the first 5 s after a start).
- The slices: `ohcamel-rt.slice` (the engine, weight 4096), `ohcamel-web.slice` (Caddy, API, hostd, 1024),
  `ohcamel-batch.slice` (worker 128, capped at 1.75 CPUs and 1280 MiB; research 256).
- **Steal** is the hypervisor giving the cycles to neighbours. Sustained steal above 10 %, or a nightly batch that
  cannot finish by 09:00 New York, is the owner's resize decision (compute plan O-1), not a tuning job.
- **The worker reads the same numbers.** Admission (`quant/src/ohcamel_quant/jobs/admission.py`) starts a job only
  when `mem_available` minus a 512 MiB reserve covers its class (S 256, M 640, L 1152 MiB), never starts a heavy
  job 09:25-16:05 New York on a session day, and gives jobs 1 thread inside that window, 2 outside. When hostd
  cannot be read, nothing starts. The worker logs each change of decision:

```bash
dc logs --since 2h ohcamel-worker | grep 'admission:'
# admission: mem_available 1843 MiB - 512 reserve -> classes MS; heavy jobs allowed; threads 2
```

  A queue that does not move with `classes none` is memory, not a bug; `host telemetry unavailable` means hostd
  is down (`dc ps ohcamel-hostd`, `dc restart ohcamel-hostd`).

## The queue: status, cancel

```bash
dc exec ohcamel-worker python -m ohcamel_quant.jobs status
```

prints `RUNNING n`, `QUEUED n` (in claim order: priority, then age) and `RECENT n` (the ten newest settled, with
cpu seconds and a failed job's error). The same data is on `/compute` and at `GET /api/jobs`.

```bash
dc exec ohcamel-worker python -m ohcamel_quant.jobs cancel <JOB_ID>
```

cancels a queued job at once, or a running one at its next heartbeat (15 s); nothing is published.

## Draining the worker

There is no pause flag. A stop sends SIGTERM: the running job gets 15 s to finish, then it is re-queued without
spending an attempt, and its child's partial work is discarded (a stopped run publishes nothing). Compose allows
25 s (`stop_grace_period`).

1. `dc exec ohcamel-worker python -m ohcamel_quant.jobs status`. If a heavy job is running (the farm takes up to
   its 90-minute budget), wait for `RUNNING 0` unless losing that run is acceptable.
2. `dc stop ohcamel-worker`. The poll is 5 s, so a queued job may be claimed between the check and the stop; it is
   re-queued the same way.
3. Queued jobs stay in `jobs.sqlite`. `dc start ohcamel-worker` resumes them; the scheduler then enqueues the
   latest missed fire of each schedule within the last 26 h, once (not one per missed fire).

A `deploy.sh --public-only` run restarts the worker the same way, so a deploy during a heavy job costs that job.

## Re-running a schedule

```bash
dc exec ohcamel-worker python -m ohcamel_quant.jobs run-schedule <NAME> [--params '<JSON object>']
```

- `NAME` is an entry in `quant/src/ohcamel_quant/jobs/schedules.yaml` (`GET /api/jobs/schedules` lists them with
  their next run). An unknown name exits 2 and lists the known ones.
- The job gets the entry's kind, priority, memory class and heavy flag, and `submitted_by = schedule:NAME`, so
  admission treats it exactly as the scheduled run: a heavy entry waits for 16:05 New York on a session day.
- It does not touch the scheduler's record (`schedule_runs`); the next scheduled fire still happens.
- A queued or running job with the same kind and params is returned, `(already live)`, instead of a second one.
- `--params` replaces the entry's params, which makes it a different job (a different `params_hash`) and, for
  products, a different artifact series.

Examples: `run-schedule risk-atlas-nightly` (P1), `run-schedule farm-nightly --params '{"budget_minutes": 30}'`,
`run-schedule exp-q01-monthly` after the universe freeze.

## Enqueueing a backfill

The warehouse fills itself through its ingest schedules: each ingest is incremental from the newest stored date
and does a full history when the table is empty. A backfill is those entries run now, in dependency order:

```bash
for n in warehouse-universes warehouse-bars-daily warehouse-fred warehouse-factors warehouse-bars-minute \
         warehouse-13f warehouse-sec-facts warehouse-option-snapshots; do
  dc exec ohcamel-worker python -m ohcamel_quant.jobs run-schedule "$n"
done
```

- **`warehouse-universes` first, and let it finish.** `ingest.bars_daily` on an empty `universe_members` fails
  (seen on 2026-10-08, when the first bars attempt raced the universes job), and the weekly universes schedule
  (Sunday 05:00) would otherwise leave it failing until Sunday. The worker runs one job at a time in priority then
  age order, so the loop's order is the run order; a failure of an earlier ingest does not stop the later ones.
- `warehouse-sec-facts` is heavy (class M) and waits for 16:05 New York on a session day.
- Option snapshots cannot be backfilled: Cboe serves only today's delayed chain, so the history starts on the
  first night it runs.
- Measured on the droplet 2026-10-08: `ingest.bars_daily` from empty wrote 563 keys in 322 cpu-s at 233 MB peak.
- Progress: `dc exec ohcamel-worker python -m ohcamel_quant.warehouse freshness` prints `data_asof` and staleness
  per dataset; `/api/warehouse/freshness` is the same.

`python -m ohcamel_quant.warehouse run <kind>` runs one ingest in the calling process, outside the queue. Do not
use it on the droplet while the worker is up: DuckDB allows one writer process, and the worker is it.

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

## Freezing the EXP-Q01 universe

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

## Restoring jobs.sqlite

`jobs.sqlite` holds the queue, the artifact ledger (which directory under `/data/artifacts` is each kind's latest)
and the scheduler's record. Losing it loses no artifact directory, but the API finds artifacts only through it.

1. Stop both writers: `dc stop ohcamel-worker ohcamel-quant`. The public site is down until step 4; the engine
   is unaffected.
2. Pick the copy: `ls -l /var/backups/ohcamel/jobs-*.sqlite` (`jobs-YYYY-MM-DD.sqlite`, made by `backup.sh` with
   SQLite's online backup API, integrity-checked, journal mode DELETE).
3. Check it and put it in place, as the image's own user (uid 10002, the volume's owner), moving the old file
   and its `-wal`/`-shm` aside rather than deleting them:

```bash
f=jobs-2026-10-08.sqlite   # the copy chosen in step 2
dc run --rm -T --no-deps -v /var/backups/ohcamel:/backups:ro --entrypoint sh ohcamel-worker -ec '
  src=/backups/$1
  python -c "import sqlite3, sys; r = sqlite3.connect(\"file:\" + sys.argv[1] + \"?mode=ro\", uri=True).execute(\"PRAGMA integrity_check\").fetchone()[0]; print(r); sys.exit(r != \"ok\")" "$src"
  cp "$src" /data/jobs.sqlite.restoring
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  for x in jobs.sqlite jobs.sqlite-wal jobs.sqlite-shm; do [ ! -e /data/$x ] || mv /data/$x /data/$x.pre-restore-$ts; done
  mv /data/jobs.sqlite.restoring /data/jobs.sqlite' sh "$f"
```

   The check prints `ok`; anything else exits non-zero before a file is touched.
4. `dc up -d ohcamel-quant ohcamel-worker`, then `dc exec ohcamel-worker python -m ohcamel_quant.jobs status`.
   A job that was `running` in the copy is re-queued by the worker's stale check after 5 minutes without a
   heartbeat (up to 3 attempts). Artifacts published after the copy was taken stay on disk but are unknown to
   the ledger, so the pages serve the copy's latest instead until the next scheduled run of each kind; nothing
   removes those directories (`prune_housekeeping` clears only `.staging`), so delete them by hand if space
   matters. To get a product back sooner, `run-schedule` it.

**The pre-registered holdouts.** EXP-Q01 (`models.xs_lgbm`) and EXP-Q02 (`regime.hmm`) evaluate their holdout
once and carry the result forward from their previous artifact (`products/experiment.py:holdout_decision`). A
ledger that no longer lists that artifact -- a copy older than the evaluation, or an empty file -- makes the next
run evaluate the holdout again, a second look the pre-registrations forbid. Before restarting the worker after
any restore, check `RECENT` and the artifact rows for both kinds; if the evaluation is gone, cancel any queued
`models.xs_lgbm` / `regime.hmm` job as soon as it appears (the scheduler re-enqueues at the next fire) and take
it to the owner.

Without a copy (the state until `/var/backups/ohcamel` exists): move the file aside the same way and start the
services. The worker creates an empty `jobs.sqlite`; every product page shows NOT YET RUN until its schedule runs
again, and `run-schedule` brings each back sooner. The P4 farm then restarts from no carried-forward cells, P7
from no surface history (the snapshots in the warehouse are reprocessed, so only history whose snapshots were
lost is gone), and the holdout rule above applies.

## Restoring the warehouse

`/data/warehouse.duckdb` has no backup: every dataset in it except `option_snapshots` can be fetched again from
its vendor, and that re-fetch is the restore.

1. `dc stop ohcamel-worker` (the only writer). From step 2 until the first ingest recreates the file,
   `/api/warehouse/freshness` answers 503 `warehouse_unavailable`.
2. Move it aside: `dc run --rm -T --no-deps --entrypoint sh ohcamel-worker -ec 'mv /data/warehouse.duckdb
   /data/warehouse.duckdb.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)'` (and `warehouse.duckdb.wal` if present).
3. `dc start ohcamel-worker`; the first ingest creates the file and applies
   `warehouse/migrations/001_init.sql`.
4. Run the backfill above.

What does not come back: `option_snapshots` (Cboe has no history), so P7's surface history survives only as
its latest `vol.surface_history` artifact, which carries the history forward; keep that artifact directory and
its `jobs.sqlite` row. A corrupt but readable old file can still be copied out with DuckDB before it is
discarded; nothing on the droplet automates that.
