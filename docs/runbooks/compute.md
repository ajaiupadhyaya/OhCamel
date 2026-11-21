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
| `/data/warehouse.duckdb` | worker's `ingest.*` jobs only; the API opens it read-only | nightly export, `deploy/backup.list` (`export`): `warehouse/YYYY-MM-DD/`, the 14 newest and each month's newest kept (Task 3.3; `docs/runbooks/restore-drill-2026-11.md`) |
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

## Wall limits (completion task 3.6)

Every job has a wall-clock limit, from its kind (`quant/src/ohcamel_quant/jobs/kinds.py`): class S 10 minutes,
class M 45 minutes, class L its compute budget plus 10 (`farm.sweep` 90 + 10, or the `budget_minutes` it is run
with plus 10; `kronos.shadow` 45 + 10). A run past it is killed with its whole process group (anything it started
goes too, and the warehouse's writer lock with it) and the job is `failed` with an error that begins `wall limit:`;
the worker takes the next job. Ingest kinds are retried twice, like any ingest failure.

A backfill runs as its schedule's kind, so an S-class backfill (`ingest.corp_actions {"full": true, "missing":
true}`, `ingest.bars_daily {"raw_missing": true}`) is held to 10 minutes a run. Both are resumable: each attempt
commits ticker by ticker, a killed attempt loses at most the ticker it was writing, and the two retries continue
from there -- about 30 minutes of work for one enqueue. If `status` then shows it `failed` with `wall limit`,
enqueue the same command again until its notes say nothing is left.

**Every ingest pass walks its keys stalest-first** (fix round 1): by the last time each key was attempted
(`ingest_log`), never-attempted first. After a complete night that is the night before's order, unchanged; after a
pass killed at its limit, the retry begins with the keys the killed attempt never reached, so the retries and the
next night cover the whole universe instead of dying at the same ticker. What a complete pass fetches and stores
is unchanged. This holds for every `run_ingest` pass: daily and minute bars, corporate actions, option chains, SEC
facts, 13F, FRED, factors, universes. The exchange calendar is refreshed before the bars pass, not after it, so a
killed bars pass cannot starve it.

**Before the deploy that brings the limits**, on the droplet (read-only: it opens `jobs.sqlite` with `mode=ro`):

```bash
dc exec ohcamel-worker python -m ohcamel_quant.jobs wall-times            # --days N, default 30
```

One line per kind: runs, p95 and max wall time of the finished runs (done or failed) in the window, the kind's
wall limit (an L kind's from its newest run's budget), and `OVER 70%` where the slowest run took more than 70% of
its limit. A flagged kind is the owner's call before the deploy (raise its class or budget, or accept the kill);
no limit was raised for it here.

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

## After a deploy that brings a warehouse migration

Migrations (`quant/src/ohcamel_quant/warehouse/migrations/NNN_*.sql`) are applied by the first process that
opens the warehouse to write. Most take an instant. **003 (completion task 3.4)** replaces `option_snapshots` (its
key gains `pull`): it renames the old table to `option_snapshots_v2`, creates the new one, and then moves the rows
one snapshot day per transaction -- measured at the writer's 256 MB: 4.8 million rows in 11 s, 12 million in 27 s
on a laptop; allow two or three times that on the droplet. Run it yourself, off-hours, right after the deploy that
brings it, so it does not happen inside whichever ingest opens the writer first (possibly the 10:00 minute-bar run
in session, which would lock the warehouse for that time: API 503s, `signals.overlay` and `risk.mc_intraday`
failing their 2 s reader open):

```bash
dc exec ohcamel-worker python -m ohcamel_quant.jobs status        # no ingest running or queued
dc exec ohcamel-worker python -m ohcamel_quant.warehouse migrate   # prints {version, days_moved, rows_moved, seconds}
```

Run it outside 09:25-17:10 New York and away from the 06:30 UTC export. It opens the file as the writer, waits up
to 30 s for a reader or an ingest to let go, and fails (nothing changed) if one does not. Run again, it prints
`days_moved: 0`.

If it is skipped, the first writer does the same work inside its job. If that process dies part way, the move
resumes on the next writer's open, and until it completes every reader of the table refuses rather than read a
part: `vol.surface_history` publishes no new day and says why, the warehouse freshness marks `option_snapshots`
`migrating` (the Machine's option feed shows that reason), and the nightly backup's `option-snapshots` step fails
and keeps the previous parquet. Running `migrate` finishes it.

**004 (completion task 3.5)** only creates two empty tables, `universe_membership` and `fred_vintages`: instant,
nothing moved. Run `migrate` with the same two commands anyway, so the version is in place before an ingest opens
the file. Then fill them -- neither fill happens inside the migration:

- `universe_membership` fills on the next `ingest.universes` (Sunday 05:00 New York) from the committed lists; to
  have it now, `dc exec ohcamel-worker python -m ohcamel_quant.warehouse run ingest.universes` (seconds).
- `fred_vintages` fills on the next `ingest.fred_warehouse` (07:00 New York daily). With `FRED_API_KEY` in the
  worker's environment that run fetches every UNRATE vintage ALFRED has (exact release dates) and nothing else is
  needed. Without the key it records only that day's vintage, and the history needs the one-off backfill below.
  Until UNRATE has vintages the recession page shows no Sahm rule (it says why) rather than one computed on
  revised figures.

```bash
dc exec ohcamel-worker python -m ohcamel_quant.warehouse backfill-vintages   # prints {series, rows, source, batches}
```

With the key it is one request set. Without it, it reads ALFRED's graph download on every Friday from UNRATE's
first vintage (1960-03-15), 12 Fridays a request (ALFRED answers no more), about 290 requests; each batch is its
own short write and logs its last Friday (`ingest_log`, dataset `fred_vintages`, key `backfill:UNRATE`), so a
killed run resumes where it stopped when run again. A value is dated by the first Friday that shows it -- on its release day (the Employment
Situation comes out on a Friday) or up to six days after, never before. Run it off-hours, away from the 06:30 UTC
export; it holds the writer only while a batch is written. `fred_vintages` is append-only: nothing in it is ever
deleted or updated, a re-run appends only rows that say something new.

**005 (completion task 3.6)** only creates one empty table, `exchange_calendar`: instant, nothing moved. Run
`migrate` with the same two commands. It fills on the next weekday `ingest.bars_daily` (18:30 New York), which
fetches Alpaca's `/v2/calendar` with the worker's `APCA_*` keys (from 1990 the first time, then the last month
and the next 400 days each night); to have it now:

```bash
dc exec ohcamel-worker python -m ohcamel_quant.warehouse calendar   # prints the run's notes; FAILED names the reason
```

Until it fills, `GET /api/warehouse/freshness` answers `calendar.source: "weekdays"` with a note, session datasets
are judged on weekdays as before, and `ops.data_integrity` skips `missing_session` with a note. A host without
`APCA_*` keys fetches nothing and logs nothing: its `exchange_calendar` row reads not stale with that note (an
announced fallback, not a fault lamp). With keys, a failed fetch is logged failed -- a fault lamp, as a fault --
and is still only a note on the bars run, never its failure; the last cached calendar stays in use. The dataset's
data date is today at most (the calendar runs about 400 days ahead; the last session cached is in the log detail).

The integrity lamp after this deploy: `flat_bar` (tier-1 ETFs only) and `vendor_change` gate when they fall in
the last 10 sessions; `missing_session` gates in that window only on a tier-1 name or when three or more tickers
miss the same session -- one single stock without a session is most likely halted all day, and is listed only.
Older findings of all three are listed only. `stale_minute`, `stale_options` and `stale_fred` never gate (the
freshness lamps carry a stale feed). Read the first night's findings before trusting a red lamp.

**006 (completion task 3.8)** creates two empty tables, `membership_series` and `delisted_acceptance`, and changes no
existing table, so a rollback to the code before it keeps writing `universe_membership`: instant, nothing moved. Run `migrate` with the same two commands, then
`dc exec ohcamel-worker python -m ohcamel_quant.warehouse run ingest.universes` (seconds) so the membership is
rewritten with the rename map and each row's series (until then a row has no series and is read as its ticker, the
old behaviour). Nothing else changes: the delisted-names source stays off until the steps under "Delisted names".

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

### Raw bars and corporate actions (completion task 3.2)

Migration `002_raw_bars.sql` adds `bars_raw`, `corp_actions` and `corp_action_revisions`; the first ingest after
the deploy applies it (three empty tables: no rewrite of `bars_daily`, no long lock). From then on the nightly
`warehouse-corp-actions` (18:20) and `warehouse-bars-daily` (18:30) keep both tables current, but only from the
deploy onward. The history is one backfill, in this order, through the queue:

```bash
dc exec ohcamel-worker python -m ohcamel_quant.jobs run-schedule warehouse-corp-actions --params '{"full": true, "missing": true}'
dc exec ohcamel-worker python -m ohcamel_quant.jobs run-schedule warehouse-bars-daily --params '{"raw_missing": true}'
```

- The first asks Yahoo for every universe ticker's splits and dividends since 1990: one ~50 KB response per
  ticker (monthly bars, `events=div|split`), ~560 requests, expected well under 15 minutes.
- The second re-fetches, in full, every ticker with no raw bar yet: the same requests as the 2026-10-08 bars
  backfill (563 keys, 322 cpu-s, 233 MB peak), so expect 15-30 minutes. It rewrites `bars_daily` with the
  vendor's current series (what the nightly run does on any re-adjustment) and writes `bars_raw` beside it.
- Both are resumable: `missing` / `raw_missing` select only the tickers not yet done, so a killed run is
  finished by enqueueing it again. Both are retried twice by the queue, like every ingest. One exception:
  a Stooq-served ticker never gets raw bars (Stooq lists no splits), so `raw_missing` re-fetches every such
  ticker on every run and never reports "nothing to do". Its notes give the count; stop when only those remain
  (the `adjust_not_comparable` count of `ops.data_integrity`).
- When: never inside the evening chain (18:20 corporate actions, 18:30 bars, 18:40-18:50 signals and overlay,
  19:15 integrity). The worker runs one job at a time, so a 15-30 minute backfill there delays every one of
  them, and the signals the desk sizes from. Quiet windows in schedules.yaml: weekdays 00:45-04:00 New York
  (after the 23:00 farm's 90-minute budget, before the 04:05 self-test), or Saturday 10:00 to Sunday 01:30.
- Progress: `ops.data_integrity`'s notes name the series with no raw bars yet and those with no corporate-action
  history yet; once both lists are empty, `adjust_drift` compares every series.

**What gates.** An `adjust_drift` finding fails the integrity lamp when it is inside the last 10 sessions OR
larger than the vendor band, 5e-6, at any date (an old missing split is a defect today). Under the band, an
old finding is listed only: Yahoo's own float32 figures wander up to 1.7e-6 from the exact convention on
1990-onward histories (task-3.2 report). Bars where Yahoo's convention is known to differ (KO 2001-09-10, an
ex-date while the market was shut) are named in `DRIFT_EXCEPTIONS` and reported with the reason, never gating.
The summary's `adjust_max_drift` shows the largest non-excepted drift every night, so the band is evidenced.

**The cut-over criterion ("30 days clean").** 30 consecutive weekday `ops.data_integrity` runs, each with:
`adjust_no_raw = adjust_no_actions = adjust_failed = 0`; `adjust_compared` equal to the warehouse series minus
the Stooq-served ones (`adjust_not_comparable`); `adjust_raw_gaps = 0`, or each such series named and
explained; `n_adjust_drift_gating = 0`; and no `adjust_drift` finding above the band at any date except the
named exceptions (`adjust_max_drift <= 5e-6`). Then a follow-up commit stops writing `bars_daily` and points
`warehouse/readers.py` at `warehouse/adjust.py`.

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

## The backup's catch-up (completion task 3.6)

`ohcamel-backup.timer` no longer fires at boot (`Persistent=false`). `ohcamel-backup-catchup.timer` checks 5
minutes after every boot and every 15 minutes after: if no backup has finished since the latest 06:30 UTC, it runs
`deploy/backup.sh --catch-up` -- but not inside 09:25-17:10 New York on a weekday, not within an hour before a
warehouse writer's schedule, and not beside a running backup (it says which, and the next check tries again).
Install once, after the deploy that brings it:

```bash
sudo install -m 0644 deploy/systemd/ohcamel-backup.timer deploy/systemd/ohcamel-backup-catchup.service \
  deploy/systemd/ohcamel-backup-catchup.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ohcamel-backup-catchup.timer
systemctl list-timers 'ohcamel-*'          # the catch-up's next check is listed
journalctl -t ohcamel-backup-catchup -n 5  # "nothing to catch up", a hold and why, or a run
```

**While the host still runs the backup from the `ohcamel` user's crontab** (docs/status.md, "What the owner
holds": no sudo for `ohcamel` since 2026-11-03), add one line to that crontab (`crontab -e` as `ohcamel`) beside the
06:30 UTC backup line; it checks every 15 minutes, so a missed backup starts within 15 minutes of boot:

```cron
@reboot sleep 300; /home/ohcamel/OhCamel/deploy/backup.sh --catch-up >>$HOME/backup.log 2>&1
*/15 * * * * /home/ohcamel/OhCamel/deploy/backup.sh --catch-up >>$HOME/backup.log 2>&1
```

The `@reboot` line is the one that keeps "within 15 minutes of boot": a `*/15` check that falls in the first
minute after boot can find the docker daemon not answering yet, and then waits for the next quarter hour. Under
cron and under systemd alike, every run that copies takes one lock (`flock`, `/var/backups/ohcamel/.state/lock`,
the same path for every user, root's hand run included), so the catch-up can never start beside the nightly run.
If `.state` cannot be written (it was created by another user without the group's write bit), the catch-up
refuses and says so, rather than run a full backup every 15 minutes without a lock or a record: fix it with
`sudo chgrp ohcamel /var/backups/ohcamel/.state && sudo chmod 2770 /var/backups/ohcamel/.state`.
`deploy/pull-backups.sh` does not pull `.state/`. When the units replace cron (O12), remove these two lines with
the other two.

The first check after install finds no run remembered (`/var/backups/ohcamel/.state/last-run` is
written by every run from this deploy on) and, outside the windows above, runs one backup: expected, and
harmless (the day's copies are replaced, the export only after it verifies).

### The calendar fixture (unblocks two tests)

`quant/tests/test_warehouse_freshness.py::test_the_day_after_thanksgiving_half_session_is_not_stale` and
`quant/tests/test_warehouse_adjust.py::test_a_session_missing_from_a_series_gates_on_the_cached_calendar` skip
until a real slice of Alpaca's calendar is committed (the agent's key was refused). On a machine with working
`APCA_API_KEY_ID`/`APCA_API_SECRET_KEY` (paper keys are enough):

```bash
cd quant && uv run --frozen python -m ohcamel_quant.warehouse calendar-slice 2026-11-02 2026-12-31 tests/data
uv run --frozen pytest -q tests/test_warehouse_freshness.py tests/test_warehouse_adjust.py   # no skips
```

It writes `alpaca_calendar_2026-11-02_2026-12-31.json` (the response body, unmodified) and its `.meta.json`
(synthetic false, source, fetch instant in UTC, sha256); neither file names the keys. Commit both.

## Delisted names (completion task 3.8, spec R5)

A universe of today's index members holds only the survivors (docs/CHARTER.md, Data). What the code does about it:

- **Backtests over dated index membership** (`run_members_backtest`: the investable set is the index's members on
  each date) are **refused** until the warehouse carries the companies that LEFT the index. That lifts only when all
  three hold: the switch is on, the acceptance test has passed on your key, and at least 95% of the S&P 500's exits
  since 2011 have prices across their stint to the exit. Every such result then says the coverage it got, and
  reports beside its headline a lower bound in which each holding whose series ended is marked to zero.
- **Hand-picked baskets still run** (`POST /api/backtest/run` and the sweep, walk-forward and cost pages over any
  tickers). A basket with any name that is not one of the site's universes.json instruments is LABELLED: its first
  note, and the run's `survivorship` field, say the names are today's survivors chosen with hindsight and that the
  result overstates what was achievable. It is not refused.

The source is EODHD, behind a switch that is **off by default**: with `OHCAMEL_DELISTED_SOURCE` unset,
`ingest.delisted` is not scheduled and refuses to run, and `run_members_backtest` refuses.

**What to buy.** EODHD "EOD Historical Data — All World", about **$20 a month** in November 2026 third-party listings
(the spec's R5). Check the live price on https://eodhd.com/pricing before paying; the plan must include delisted US
tickers. Nothing is bought, subscribed or spent by the agent. The optional second item in R5 is below.

**Where the key goes.** `/etc/ohcamel/live.env` (root:ohcamel 0640), next to the Alpaca and FRED keys: it is the file
the worker and the quant containers read (`env_file` in deploy/docker-compose.yml). Not `deploy/.env`, which by its
own header holds no secrets and is not passed to the containers. One line first, the key only:

```bash
sudoedit /etc/ohcamel/live.env          # add:  EODHD_API_KEY=<your key>
dc up -d ohcamel-worker ohcamel-quant   # recreate both so they read it (compose sees the changed env file)
```

**The one command: the acceptance test**, with the switch still off (it needs only the key):

```bash
dc exec ohcamel-worker python -m ohcamel_quant.warehouse accept-delisted
```

It is `tools/accept_delisted.py` (the image has no `tools/` directory, so on the droplet it runs as the module). It
asks EODHD for each of the 26 known failures and acquisitions in
`quant/src/ohcamel_quant/warehouse/delisted_acceptance.csv` (Lehman, Bear Stearns, Washington Mutual, SVB, First
Republic, Bed Bath & Beyond and twenty more, each with its last trading date and the notice it came from), 26
requests, under a minute. A name passes when it has a bar within 5 weekdays of its last trading date (a series with
no bar on or before that date fails). For a failure, prints after the date are fine when they continue without a gap
-- the same company carrying on, as when the vendor stitches the over-the-counter continuation onto the exchange code
(the verdict says it ran on); for an acquisition, whose ticker never continues, prints running more than 5 weekdays
past the date fail. Prints that resume after a gap of more than 5 weekdays fail for both (another company under the
code). It prints one PASS/FAIL line per name. At 24 of 26
or better it prints ACCEPTED, records `accepted_at` in the warehouse and exits 0; otherwise NOT ACCEPTED, exit 1,
nothing recorded. **Exit 3, "PASSED, NOT RECORDED"**, means the names passed but the warehouse stayed busy past the
writer's 30-second wait (an ingest was writing): it is not a failure -- run the command again, away from the 18:20-19:15
weekday ingests and the 06:30 UTC export.

A failed company usually moved to a Q-suffixed over-the-counter code after its exchange delisting (LEHMQ, WAMUQ,
SIVBQ, BBBYQ, ENDPQ; ANRZ for Alpha Natural Resources). A vendor that files a failure's history under that code
rather than the exchange ticker makes both this test and `ingest.delisted` miss the name -- a correct failure, since
the warehouse asks by the exchange ticker.

**If it fails:** cancel the subscription, take the `EODHD_API_KEY` line out of `/etc/ohcamel/live.env` and recreate
the two containers. The switch stays off and nothing else changes: backtests over dated membership stay refused and
hand-picked baskets stay labelled, as now.

**If it passes:** turn the switch on and backfill.

```bash
sudoedit /etc/ohcamel/live.env          # add:  OHCAMEL_DELISTED_SOURCE=eodhd
dc up -d ohcamel-worker ohcamel-quant
dc exec ohcamel-worker python -m ohcamel_quant.jobs run-schedule warehouse-delisted
dc exec ohcamel-worker python -m ohcamel_quant.warehouse survivorship sp500   # switch, accepted, coverage, verdict
```

`warehouse-delisted` then runs every Sunday at 04:15 New York (the brief's 04:00 is `ingest.sec_facts`' minute). The
first run asks for every S&P 500 exit since 2011 without prices to it -- about 320 series, three requests each (bars,
splits, dividends), well inside the plan's daily request allowance (check it on the plan page) and inside the M
class's 45-minute limit; if the limit kills it, the worker's retry carries on with the series still missing (a
series written is covered and not asked again). Later Sundays ask only for new exits. It writes `bars_raw`,
`corp_actions` and the derived `bars_daily` with `source = 'eodhd'`, the whole series from one vendor: a series
another vendor already serves is left out with a note, never mixed. Left out too, with a note: the earlier holder
of a ticker a later member company reused (`TICKER@DATE` in `membership_series`, for example Allergan Inc.,
`AGN@2015-03-23`), because EODHD's code for it is not documented. Such names count as uncovered. Each series is asked
for up to 100 days past its exit, so a name removed from the index but still trading keeps a live price until the
next rebalance sells it; a vendor series that begins after the company joined the index is refused (another company
under the code), and one is cut at the first gap of more than 5 weekdays on or after the exit (past it the code may
be another company's; the cut is in the key's `ingest_log` detail as `truncated`).

`survivorship` prints the coverage. Below 95% the refusal stands and says "N% of members have prices to their exit";
the uncovered exits are the ones `ingest.delisted`'s notes list (vendor gaps, reused tickers, another vendor's
short history).

**Record the delisted fixture (unblocks one test).** `quant/tests/test_data_eodhd.py::test_the_adapter_parses_a_delisted_response`
skips, BLOCKED, until a real delisted-ticker response is committed (the agent had no paid key; EODHD's free
demonstration key serves only a few listed tickers, which is how the AAPL fixture was recorded). On a machine with
the key:

```bash
cd quant && EODHD_API_KEY=<key> uv run --frozen python -m ohcamel_quant.warehouse eodhd-slice LEH.US 2008-08-01 2008-10-31 tests/data --delisted --last-trading-date 2008-09-17
uv run --frozen pytest -q tests/test_data_eodhd.py   # no skips
```

It writes the eod, splits and div responses unmodified, each with a `.meta.json` (synthetic false, URL and
parameters without the key, fetch instant in UTC, sha256); no file names the key. If EODHD keeps Lehman under
another code, use that code. Commit the six files.

**Cost.** About $20 a month, nothing else: the requests are inside the plan, and the warehouse grows by the
delisted names' daily bars (about 320 series of up to 35 years, a few million rows, tens of MB in DuckDB).

**Optional, not built: whole-market minute bars.** R5's second item is Massive (formerly Polygon) Stocks Starter,
about $29 a month, for minute bars from the whole market in place of IEX's 2-3%. Its switch would be
`OHCAMEL_MINUTE_SOURCE=massive`. This plan documents it and does not build it: nothing reads that variable, and
setting it changes nothing. Full SIP at $99 does not fit the budget.

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

Since Task 3.3 the warehouse is exported every night (`deploy/backup.sh`, the `export` line of
`deploy/backup.list`) to `/var/backups/ohcamel/warehouse/YYYY-MM-DD/`, and that export is the restore:
`deploy/restore.sh --warehouse /var/backups/ohcamel/warehouse/YYYY-MM-DD` writes a NEW file under
`/var/backups/ohcamel/restored/`; with the worker and the API stopped,
`deploy/restore.sh --warehouse ... --replace-live --services-stopped` replaces `/data/warehouse.duckdb` (the old
file moved aside, never deleted). `docs/runbooks/restore-drill-2026-11.md` is the drill and its sizes.

Without an export (before the first one, or every export lost), every dataset except `option_snapshots` can be
fetched again from its vendor, and that re-fetch is the restore:

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
