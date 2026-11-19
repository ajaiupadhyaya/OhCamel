# Restore drill, 2026-11: the warehouse export and a regenerated number

Task 3.3 ("a number can be regenerated"). The warehouse is exported every night by `deploy/backup.sh` (the
`export` line of `deploy/backup.list`) into `/var/backups/ohcamel/warehouse/YYYY-MM-DD/`; every artifact's
manifest names the data it read (`data_hash`, with each read in `reads.json` beside it), its seeds (`seed`), the
lock file it was installed from (`lock_hash`), its `threads` and its kernels; and `tools/regenerate.py` re-runs a
job from an artifact and an export and exits non-zero unless every table matches (floats within 1e-9).

The drill is one script, `deploy/warehouse-drill.sh`:

    export -> wipe -> restore (deploy/restore.sh --warehouse) -> regenerate (tools/regenerate.py) -> PASS,
    then one altered row in a copy of the export -> regenerate must FAIL

This file holds two runs. The first was made on the **fixture warehouse, on the development machine**, because
the droplet is not to be contacted from an agent session and no Docker daemon was running there. The second --
**last night's real backup** -- is the owner's to run, and is not filled in.

## 1. Run on the fixture warehouse, on this machine (2026-11-19, re-run after fix round 2)

Not production data. The fixture warehouse is the nine committed ETFs (`fixtures/history`, 2016-06-01 to
2026-06-01), three FRED series and the universe definitions, built by
`python -m ohcamel_quant.warehouse build-fixture` with the bars logged as full-history fetches
(`--requested-start 1990-01-01`) so the market facade serves the job from the warehouse rather than falling back
to the committed fixtures. The published number is a small `risk.mc_atlas` (20,000 paths; members SPY, QQQ, TLT)
with its clock pinned at the fixtures' last session (2026-06-01 17:00 New York), so the warehouse counts as fresh.

- Machine: macOS (Darwin, arm64), no Docker daemon, no network used.
- Software: the repository's `quant/.venv` -- Python 3.13.12, DuckDB 1.5.6, pandas 3.0.6, pyarrow 25.0.1,
  numpy 2.5.3. No Rust wheel in this venv, so the kernels ran on the NumPy reference (the manifest says
  `kernels {'engine': 'python'}`; in the image they run on Rust and the manifest says so).
- Code: the working tree that became Task 3.3's fix-round-2 commit, on top of `b5a96fe` (fix round 1). The
  manifest's `code_sha` reads `b5a96fe...` because the run preceded the commit -- the artifact and the re-run used
  one checkout, which is what regenerate's code check asks.
- Command, from the repository root: `deploy/warehouse-drill.sh --fixture $SCRATCH` (a new directory).
- The transcript below is the script's output verbatim, except that the scratch directory's path is written
  `$SCRATCH` and the repository's `$REPO` (both were long absolute paths); the first line is the start time.

```text
2026-11-19T14:47:52Z

== drill: 1. the warehouse: the fixture warehouse (the committed ETFs, logged as full-history fetches)
$ python -m ohcamel_quant.warehouse build-fixture $SCRATCH/live/warehouse.duckdb --requested-start 1990-01-01
$SCRATCH/live/warehouse.duckdb

== drill: 2. a published number: risk.mc_atlas on it, the clock pinned at the fixtures' last session
$ python -m ohcamel_quant.jobs.local risk.mc_atlas --artifacts $SCRATCH/artifacts --warehouse $SCRATCH/live/warehouse.duckdb --offline --now 2026-06-01T21:00:00Z --params '{"n_paths": 20000, "member_paths": 20000, "members": ["SPY", "QQQ", "TLT"]}'
ohcamel_kernels is not importable (No module named 'ohcamel_kernels'); kernels run on the NumPy reference
artifact risk.mc_atlas 01KT2FRZ40292DVW9F8EWYA86D
  data_hash 3408ae76934dc9b485bb14e605a1f9414c7a75ec5eb4db51c493ace755406b57
  seed {'seed': 20261006}  lock_hash 333652225230a743b566de79c7ce14c1ed3a07b3a7d85aff4a4ebdff687ce875  threads 1  kernels {'engine': 'python', 'api': None}
  reads 4 - warehouse:fixture:alpaca (reads.json)

== drill: 3. the nightly export, as backup.sh runs it (read-only EXPORT DATABASE, verified, renamed)
$ python -m ohcamel_quant.warehouse export $SCRATCH/live/warehouse.duckdb $SCRATCH/backups/warehouse --date 2026-06-01
export: $SCRATCH/backups/warehouse/2026-06-01 -- 13 tables, 31118 rows, 488602 bytes, schema version 2, verified
export took 403 ms; on disk: 528 KiB; warehouse file: 3084 KiB

== drill: 4. wipe: the warehouse is deleted; only the export is left
$ rm $SCRATCH/live/warehouse.duckdb

== drill: 5. restore, through deploy/restore.sh, into a new file
$ deploy/restore.sh --warehouse $SCRATCH/backups/warehouse/2026-06-01 --local-python $REPO/quant/.venv/bin/python --to $SCRATCH/restored/warehouse.duckdb
restore: $SCRATCH/restored/warehouse.duckdb -- 13 tables, 31118 rows, every count equal to the export's record, schema version 2
restore: warehouse restored from $SCRATCH/backups/warehouse/2026-06-01 into $SCRATCH/restored/warehouse.duckdb (a new file; nothing live was touched)

== drill: 6. regenerate the published number from the export
$ python tools/regenerate.py $SCRATCH/artifacts/risk.mc_atlas/01KT2FRZ40292DVW9F8EWYA86D --warehouse $SCRATCH/backups/warehouse/2026-06-01
regenerate: risk.mc_atlas 01KT2FRZ40292DVW9F8EWYA86D (started 2026-06-01T21:00:00.000Z, data_hash 3408ae76934dc9b4..., seed {'seed': 20261006}, threads 1)
ohcamel_kernels is not importable (No module named 'ohcamel_kernels'); kernels run on the NumPy reference
regenerate: code b5a96fec51d9, lock 333652225230, kernels python: as the manifest names them
regenerate: export $SCRATCH/backups/warehouse/2026-06-01 of 2026-06-01 (schema version 2, 31118 rows)
regenerate: data_hash matches (4 reads)
regenerate:   summary: matches
regenerate:   euler: matches
regenerate:   members: matches
regenerate: every table matches (3 tables, floats within 1e-09)

== drill: 7. the negative check: one altered row in a copy of the export must fail the re-run
altered bars_daily.adj_close by 1 part in 10^4 where ticker = 'SPY' AND date = DATE '2026-05-29' (1 row(s), bars_daily.parquet)
$ python tools/regenerate.py $SCRATCH/artifacts/risk.mc_atlas/01KT2FRZ40292DVW9F8EWYA86D --warehouse $SCRATCH/altered/2026-06-01
regenerate: risk.mc_atlas 01KT2FRZ40292DVW9F8EWYA86D (started 2026-06-01T21:00:00.000Z, data_hash 3408ae76934dc9b4..., seed {'seed': 20261006}, threads 1)
ohcamel_kernels is not importable (No module named 'ohcamel_kernels'); kernels run on the NumPy reference
regenerate: code b5a96fec51d9, lock 333652225230, kernels python: as the manifest names them
regenerate: export $SCRATCH/altered/2026-06-01 of 2026-06-01 (schema version 2, 31118 rows)
regenerate: data_hash differs: the export does not hold the data the job read (no table was compared). An artifact can be regenerated only from the first export after it, before the next ingest touches what it read -- use that night's export
  - different rows: market.returns{"args":[["SPY","QQQ","IWM","TLT","IEF","GLD"]],"kwargs":{}}
  - different rows: market.returns{"args":[["SPY"]],"kwargs":{}}
regenerate exited 4 on the altered export, as it must

== drill: PASS -- export, wipe, restore and regenerate on the fixture warehouse; the altered export was caught
deploy/warehouse-drill.sh --fixture $SCRATCH  5.16s user 0.69s system 99% cpu 5.888 total
exit 0
```

What it shows: the export took 403 ms for 31,118 rows (13 tables) and is 528 KiB on disk against a
3,084 KiB DuckDB file; the restore came back with every table's count equal to the export's record and the
migration version (2) intact; the re-run from the export matched every table of the artifact, its `data_hash`
included; and one adjusted close changed by one part in ten thousand in a copy of the export was caught before
any table was compared, naming the two reads it changed. Every run made while building this gave one data hash
for one tree: `23cea878...` before fix round 1, `3408ae76...` after it, when each read's name was bound into the
hash (`name<TAB>sha256` lines; the read hashes themselves did not change).

## 2. OWNER: run on last night's backup (not yet filled in)

This is the drill on real data. Nothing below has been run; fill it in with the commands' output.

1. Pick a regenerable artifact made by the code that is deployed now (only artifacts published after the Task 3.3
   deploy carry a `data_hash`), made the evening before the export you will use: an artifact regenerates only from
   the first export after it, before the next ingest touches what it read. A product that reads only the warehouse is the right first choice --
   `vol.forecast_league`, `cov.league`, `ops.data_integrity` or `factors.screen`; `regenerate.py` refuses, by name,
   any read the warehouse did not serve (a vendor fallback, the live engine), and refuses the kinds that cannot
   be re-run at all (the ingests, the signal producers, `desk.report`, `kronos.shadow`, `farm.sweep`,
   `ops.selftest`). On the droplet:

       docker compose -f deploy/docker-compose.yml exec ohcamel-worker python -m ohcamel_quant.jobs status
       docker compose -f deploy/docker-compose.yml exec ohcamel-worker sh -c 'ls -t /data/artifacts/vol.forecast_league | head -3'

2. Either on the laptop (after `deploy/pull-backups.sh` has brought `warehouse/YYYY-MM-DD` down, and with the
   artifact directory copied out of the `quant_data` volume beside it, e.g.
   `docker compose -f deploy/docker-compose.yml cp ohcamel-worker:/data/artifacts/<kind>/<id> ./artifact`):

       deploy/warehouse-drill.sh --export ~/Backups/ohcamel/warehouse/YYYY-MM-DD --artifact ./artifact \
           --alter "bars_daily:adj_close:ticker = 'SPY' AND date = DATE 'YYYY-MM-DD'"

   The laptop's checkout must be at the deployed SHA (`git checkout <sha>`) with `quant/.venv` synced from that
   SHA's `uv.lock`, or `regenerate.py` refuses (exit 3) -- that refusal is correct, not a failure of the drill.
   The laptop runs the kernels on whatever its venv has; an artifact made on Rust is refused on the NumPy
   reference (random kernels agree across engines only in distribution). `make kernels-dev` installs the wheel.

   Or on the droplet, in the Quant image (it has the same code, lock and Rust kernels as the worker that made
   the artifact), with the scratch on disk, not in a tmpfs:

       sudo install -d -m 0770 -o 10002 -g ohcamel /var/backups/ohcamel/drill
       OHCAMEL_TAG=<deployed sha> OHCAMEL_IMAGE=ghcr.io/ajaiupadhyaya/ohcamel-quant OHCAMEL_BACKUP_VOLUME=quant_data \
         docker compose -f deploy/docker-compose.yml --profile backup run --rm -T --user 10002:$(id -g) \
         --entrypoint python ohcamel-backup -m ohcamel_quant.jobs.regenerate /data/artifacts/<kind>/<id> \
         --warehouse /backups/warehouse/YYYY-MM-DD --scratch /backups/drill/run-1
       deploy/restore.sh --warehouse /var/backups/ohcamel/warehouse/YYYY-MM-DD

   (`quant_data` is mounted because the artifact lives there; `regenerate` only reads it, and writes only under
   `--scratch`. The second command restores the export into a new file under `/var/backups/ohcamel/restored/`.)
   Remove `/var/backups/ohcamel/drill` and `restored/` afterwards.

   Same platform: `regenerate.py`'s 1e-9 comparison holds on the platform the artifact was made on. An
   optimizer-fitted product (GARCH by Nelder-Mead, the HMM, LightGBM) is not bit-portable -- Linux CI moved the
   vol league's fitted values against macOS (fix round 2) -- so judge a production artifact by regenerating it in
   the Quant image on the droplet (the container command above), not on the laptop; a mismatch on another
   platform says so in its message.

3. Record here: the date of the export, the artifact's kind and id, `du -sh` of the export directory, how long
   the nightly export took (`journalctl -u ohcamel-backup.service` around 06:30 UTC), the restore's counts, the
   `regenerate` transcript, and the altered-row run's non-zero exit.

```text
(OWNER: paste the transcript here)
```

## 3. What the export costs, and the retention

Measured on the fixture (above): 528 KiB for 31,118 rows; `bars_daily` is 448 KB for 22,626 rows, about 20 bytes
a row in zstd Parquet (the whole fixture DuckDB file is 3,084 KiB). A synthetic 1-minute series of the
warehouse's shape compresses to about 14 bytes a row.

Estimated for the live warehouse (no live sizes are stated anywhere this session could read, and the droplet was
not contacted; the owner's first nightly export replaces these numbers with `du -sh`):

| Table | Rows, roughly | Export |
|---|---|---|
| `bars_daily` | 563 names x ~6,000 sessions = ~3.4 M | ~70 MB |
| `bars_raw` (Task 3.2, as its backfill completes) | ~3.4 M | ~65 MB |
| `bars_minute` | 50 names x 2 years x ~98,000 minutes at most = <= 9.8 M | <= 140 MB |
| `sec_facts` | unknown; a few million | 50-200 MB |
| `option_snapshots`, `holdings_13f`, `fred`, `factors`, the rest | small, `option_snapshots` growing daily | < 50 MB |
| **one export** | | **~0.3-0.5 GB** |

Retention keeps the 14 newest exports and the newest of each calendar month (month-ends are never pruned). In the
first year that is up to 14 + 12 = 26 exports, **about 8-13 GB**, then about 0.3-0.5 GB more for each further
month-end. The droplet's disk is 77 GB (12 GB used at the 2026-09-24 survey), so it fits, but the exports become
the largest thing on it. Before each export `backup.sh` checks for twice the newest export's size free (1 GiB
before the first); short of that it prunes the oldest dailies first -- never the newest export, never a
month-end -- and if that is still not enough it refuses the export (the Ledger's backup row says
"failed at the warehouse export") rather than delete more. A failed or partial export never takes its dated name:
it is written to `YYYY-MM-DD.part`, verified, then renamed.

Time and locks: the export opens the warehouse read-only, and a read-only DuckDB process holds the shared lock, so
no writer can open the file while it runs. An ingest does not simply wait for it: `run_ingest`
(`warehouse/ingest/base.py`) opens the writer once per key with a 30 s timeout, so a key whose open times out
during an export is logged FAILED and not retried that night, and if the run's summary log write times out as
well the job fails (then its job-level retries apply). Two things keep them apart (fix round 1): the export
first reads the job queue (`/data/jobs.sqlite`, read-only) and waits while any warehouse-writing ingest is
running with a live heartbeat (a dead worker's leftover row, older than the queue's own staleness threshold,
does not count) -- up to 10 minutes, then the export fails loudly and the Ledger's backup row says "failed at the
warehouse export"; and no warehouse-writing schedule fires between 06:30 and 07:30 UTC in either New York offset
(`tests/test_jobs_schedules.py` holds it, reading the backup timer). `ingest.sec_facts` and
`ingest.holdings_13f` moved from Sunday 02:00 and 03:00 New York to 04:00 and 04:30 for this (02:00 EST is 07:00
UTC; 03:00 EDT is 07:00 UTC). An ingest started by hand during an export can still lose keys. At ~0.3-0.5 GB the
export should take tens of seconds to a couple of minutes on the droplet's shared vCPUs (estimated, not measured).
The export runs last in the backup, after every copy and the book and deploy-env copies, and `backup.sh` bounds it
at 40 minutes (the 10-minute wait plus 30 of export) inside the unit's 60-minute `TimeoutStartSec`: a slow export
is a failed step, and the status record is still written.

Which export regenerates an artifact: the first export after the artifact was made, before the next ingest
changes what it read. A later export holds later data; `regenerate.py` then reports `data_hash differs` (exit 4)
and compares no table -- that is the export not holding the data, not the number failing to reproduce.

The laptop (fix round 1): `deploy/pull-backups.sh` keeps everything else it pulls, but not every export. Its main
rsync excludes `warehouse/`; a second step lists the host's exports and pulls only the newest and each month's
newest, each into `DATE.part`, checked (export.json, load.sql, and every Parquet file load.sql names) before it
takes its name; then -- only when the newest arrived whole -- the laptop keeps its 3 newest exports and each
month's newest, and deletes the rest. A failed or partial pull prunes nothing, so the only off-box copy is never
the one deleted. That is about 3 + 12 exports a year on the laptop (~5-8 GB), not one a night.

## 4. Restoring for real

- A new file, nothing live touched: `deploy/restore.sh --warehouse /var/backups/ohcamel/warehouse/YYYY-MM-DD`
  (into `/var/backups/ohcamel/restored/warehouse-YYYY-MM-DD.duckdb`).
- Replacing the live warehouse: `docker compose -f deploy/docker-compose.yml stop ohcamel-worker ohcamel-quant`,
  then `deploy/restore.sh --warehouse /var/backups/ohcamel/warehouse/YYYY-MM-DD --replace-live --services-stopped`
  (refused if compose still reports either running; the old file stays in `quant_data` as
  `warehouse.duckdb.pre-restore-<UTC>`), then `docker compose -f deploy/docker-compose.yml up -d ohcamel-worker
  ohcamel-quant`. `schema_version` is restored with the data, so the migration runner applies only newer
  migrations.
