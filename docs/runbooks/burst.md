# Burst research: a heavy job on your machine, published on the live host

Completion task 3.7, spec ruling R6 ("Resize the droplet? No. Heavy research runs on a burst machine against the
nightly export"). R6 is a spec default the owner has not individually approved; nothing here changes the desk.

One command runs one of the four heavy research kinds -- `farm.sweep`, `models.xs_lgbm`, `cov.league`,
`risk.mc_atlas` -- on this machine, with every core, against the droplet's newest nightly warehouse export, and
uploads its artifact through the **burst door** on the live host. The artifact is published exactly where the
worker publishes, through the same manifest code, and is marked `host: burst` (the public artifacts API shows
`"host": "burst"`). It never reaches the desk: every artifact read on the desk's path (signals/, and the farm's
carry-forward the monthly freeze reads) is worker-only.

## Try it first: the dry run costs nothing

    make research-burst KIND=cov.league PARAMS='{}' DRY_RUN=1

This pulls the newest export with `deploy/pull-backups.sh` (your existing `ssh ohcamel` rsync, nothing else),
restores it into a temporary directory, runs the job here exactly as the worker's child would, builds the
manifest and the upload, prints what it would send (verdict, data_hash, tables, size), and **stops**. Nothing is
held on the droplet and nothing is uploaded; the URL, password and secret below are not needed. To skip the pull
and use an export you already have:

    cd quant && uv run --frozen python ../tools/burst.py --kind cov.league --params '{}' --dry-run \
        --export ~/Backups/ohcamel/warehouse/2026-11-19

A dry run of `farm.sweep` or `models.xs_lgbm` mirrors the droplet's earlier artifacts first (read-only, signed)
when the three variables in step 4 below are set. **`models.xs_lgbm` refuses a dry run without them**: EXP-Q01's
once-only holdout decision depends on the droplet's state. A farm dry run without them starts from no carried
state and says so.

**A burst never evaluates EXP-Q01's holdout.** If the droplet's decision would be to evaluate it (no holdout yet,
or a re-evaluation you approved with `holdout_reevaluation_approved`), the run stops before the holdout is touched,
the tool deletes its scratch directory (even with `--keep`) and says the holdout evaluation runs on the worker
only. The worker's monthly run evaluates it, and its result is always recorded. A burst that carries the
holdout forward runs normally, and the door checks the carried tables are the droplet's, unchanged. An EXP-Q01
dry run prints no verdict. The farm has no once-only evaluation (each night re-scores its cells walk-forward, and
DSR deflates by the grid's trial count, which a re-run does not change), so a farm burst or dry run spends nothing.

## One-time setup (owner, after this commit is deployed)

1. **Make the door's key.** On your machine: `openssl rand -hex 32`. Keep it in your password manager.
2. **Give the droplet the same key.** On the droplet, as your deploy user:

        sudo install -m 0640 -o root -g "$(id -gn)" /dev/null /etc/ohcamel/burst.env
        sudoedit /etc/ohcamel/burst.env        # one line: OHCAMEL_BURST_SECRET=<the key>

   Only this file reaches the door (no Alpaca or FRED key does). Without it, or with a key under 32 characters,
   the door answers every request `503 burst_off`.
3. **Deploy with the live profile, outside market hours** (`deploy/deploy.sh --sha <sha> --live`, the usual way). That
   starts `ohcamel-burst` and recreates Caddy, whose live-host block now routes `/burst/*` to it behind the
   existing basic auth. Check: `curl -u <user> https://<live host>/burst/health` answers `{"status":"ok","open":true}`.
   The public host has no such route: `https://<public host>/burst/health` is the site's 404 page.
4. **Your environment** (a shell profile or your password manager's CLI; never the repository):

        export OHCAMEL_BURST_URL=https://<live host>
        export OHCAMEL_BURST_AUTH='<basic-auth user>:<basic-auth password>'
        export OHCAMEL_BURST_SECRET=<the key from step 1>

   The tool reads these and never prints them.

## Run one

1. Check out **the commit the droplet runs**, with a clean tree (`git status` empty). The door refuses an artifact
   whose `code_sha` or `uv.lock` hash is not the deployment's, and the tool refuses a dirty tree before it starts.
   For numbers that match the droplet's to the last bits, build the Rust kernels here too (`make kernels-dev`);
   without them the run uses the NumPy reference, which the manifest records (`kernels.engine`).
2. Run it, with the job's params exactly (the schedule's, or your own):

        make research-burst KIND=farm.sweep PARAMS='{"budget_minutes": 240}'

   In order it: pulls the newest export; asks the door for a **burst job** with that kind and params (a job only
   the burst machine runs, held for the kind's wall limit plus an hour); stops at once if the droplet runs other
   code; mirrors the earlier artifacts the job reads; runs; uploads. It prints the job id, the verdict, the
   data_hash and `published <kind>/<id>`.

   **A burst never touches a scheduled run.** If a schedule's job with the same kind and params is queued or
   running, the door refuses (`409 scheduled`): the worker runs it and publishes its artifact. If a schedule fires
   while your burst job is held -- `risk.mc_atlas {}` at 20:00 on weekdays, `cov.league {}` on Saturdays,
   `farm.sweep {"budget_minutes": 90}` at 23:00, `models.xs_lgbm {}` monthly -- it gets its own job and the worker
   runs it as always; your burst job is a separate artifact. The desk reads the worker's artifacts only.
3. If it fails part way, run it again: a hold is renewed, and an upload the door already published is recognised
   as done. If you give up, nothing is needed: the burst job is cancelled when its hold expires (it is never run on
   the droplet). The door verifies one upload at a time; a second gets `429 busy` and the tool says so -- rerun it.
4. **Regenerate every burst artifact on the droplet** (outside market hours; it is a heavy run). This is what
   re-checks what the door cannot (below): that the export gives the reads the artifact names, the seeds, the
   unseeded-randomness check and the product's own gates. On the droplet, in the deploy checkout:

        OHCAMEL_TAG=$(git rev-parse HEAD) docker compose -f deploy/docker-compose.yml run --rm --no-deps \
          --entrypoint python -v /var/backups/ohcamel/warehouse:/exports:ro ohcamel-worker \
          -m ohcamel_quant.jobs.regenerate /data/artifacts/<kind>/<id> --warehouse /exports/<day> \
          --scratch /data/regenerate-<id>
        OHCAMEL_TAG=$(git rev-parse HEAD) docker compose -f deploy/docker-compose.yml run --rm --no-deps \
          --entrypoint rm ohcamel-worker -rf /data/regenerate-<id>

   `<day>` is the manifest's `burst.export.day`. `data_hash matches` must appear: the data hash does not depend on
   the platform. The tables are compared within 1e-9, which holds on the platform the artifact was made on
   (`runtime.platform`). macOS and Linux differ by up to ~6e-7 on optimizer-fitted products, so a macOS burst
   artifact can show a table difference there that is the platform, and regenerate says so. A `data_hash
   differs`, or a table difference far beyond that, means the artifact is not what it claims.

## What the door refuses, and why

| status | error | when |
|---|---|---|
| 503 | `burst_off` | the door's secret is unset or under 32 characters |
| 401 | `bad_signature`, `stale` | no HMAC or nonce, a wrong one, or a timestamp more than 5 minutes off |
| 409 | `replayed` | the same signed request received twice |
| 413 | `too_large` | a body over 64 MiB (67,108,864 bytes, in Caddy and in the door) |
| 429 | `busy` | another upload is being verified |
| 403 | `not_burstable` | any kind but the four |
| 409 | `scheduled` | a schedule's job with these params is live, or the upload names a job the door did not create |
| 409 | `claimed` | the job is running |
| 409 | `already_done` | the job is done (a retry of an upload that succeeded is recognised by the tool) |
| 409 | `not_queued` | the job failed or was cancelled |
| 409 | `job_mismatch` | the artifact was made with other params, or for another kind, than the queued job |
| 422 | `bad_manifest`, `inconsistent_manifest`, `unrecorded_reads` | the manifest is not exactly what the worker's publish would write for this job and these reads (the door rebuilds it and recomputes the data_hash) |
| 422 | `bad_file` | a file name outside the kind's fixed list, a table the kind never produces, bytes that are not Parquet, a table that would decode past the door's memory budget |
| 422 | `schema_mismatch` | a table's columns or types are not those of the droplet's newest worker artifact of the kind |
| 409 | `holdout_worker_only` | an EXP-Q01 upload carries holdout tables that are not the droplet's, unchanged |
| 409 | `not_from_export` | the run read something no export holds (a vendor, the live engine), or was served the committed fixtures because the warehouse was not fresh at the export's moment (the tool stops on this first; use an export taken after a complete ingest) |
| 409 | `code_drift` | other code or another lock file than the deployment's |
| 409 | `no_such_export` | the export it names is not one the droplet holds, byte for byte (the door hashes its own copy) |
| 409 | `stale_state` | an earlier artifact it read is not the droplet's, is no longer the newest, or is newer than the export |

What the door cannot check, plainly. It does not see the run, so it cannot check:

- that each warehouse read's hash is what that export gives for that query. Checking it would mean running the
  product on the droplet, which is what a burst avoids, or running SQL taken from the upload, which it never does.
- the gates applied while the handler runs. The door rebuilds the manifest with `unseeded=[]` and
  `unrecorded=[]`, so the check that no random number was drawn without a seed, and every verdict the product
  computes inside itself, rest on your machine running the deployed code.

Both rest on your machine (basic auth plus the key, deployed code only). Step 4's regenerate re-checks both on the
droplet, from the export the manifest names.

## Rotating or closing the door

Change the key in both places (`/etc/ohcamel/burst.env`, then `docker compose -f deploy/docker-compose.yml
--profile live up -d ohcamel-burst` on the droplet; your environment). To close it, empty the file and restart
the service the same way: every request is then `503 burst_off`.
