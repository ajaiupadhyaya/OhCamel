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

A dry run of `farm.sweep` or `models.xs_lgbm` starts from no carried state (it does not fetch the droplet's
earlier artifacts) and says so; a real run mirrors them first.

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

   In order it: pulls the newest export; asks the door to **hold** the job (the queued job with that kind and
   params, or a new one only the burst machine will run) so the worker does not run it meanwhile -- the hold lasts
   the kind's wall limit plus an hour; stops at once if the droplet runs other code; mirrors the earlier artifacts
   the job reads; runs; uploads. It prints the job id, the verdict, the data_hash and `published <kind>/<id>`.
3. If it fails part way, run it again: a hold is renewed, and an upload the door already published is recognised
   as done. If you give up, nothing is needed: a job the door created is cancelled when its hold expires (it is
   never run on the droplet); a scheduled job you held simply goes back to the worker.
4. Later, to check the number: `tools/regenerate.py <artifact dir> --warehouse <the export its burst block names>`
   on **the platform it was made on** (`runtime.platform` in the manifest). macOS and Linux differ by up to ~6e-7
   on optimizer-fitted products, so regenerating a macOS burst artifact on the droplet can show a difference that
   is the platform, and regenerate says so.

## What the door refuses, and why

| status | error | when |
|---|---|---|
| 503 | `burst_off` | the door's secret is unset or under 32 characters |
| 401 | `bad_signature`, `stale` | no HMAC, a wrong one, or a timestamp more than 5 minutes off |
| 413 | `too_large` | a body over 64 MB |
| 403 | `not_burstable` | any kind but the four |
| 409 | `claimed` | the worker is already running that job (its own artifact will be published) |
| 409 | `already_done` | the job is done (a retry of an upload that succeeded is recognised by the tool) |
| 409 | `not_queued` | the job failed or was cancelled |
| 409 | `job_mismatch` | the artifact was made with other params, or for another kind, than the queued job |
| 422 | `bad_manifest`, `inconsistent_manifest`, `unrecorded_reads` | the manifest is not exactly what the worker's publish would write for this job and these reads (the door rebuilds it and recomputes the data_hash) |
| 422 | `bad_file` | a file name outside the kind's fixed list, a table the kind never produces, bytes that are not Parquet |
| 409 | `not_from_export` | the run read something no export holds (a vendor, the live engine), or was served the committed fixtures because the warehouse was not fresh at the export's moment (the tool stops on this first; use an export taken after a complete ingest) |
| 409 | `code_drift` | other code or another lock file than the deployment's |
| 409 | `no_such_export` | the export it names is not one the droplet holds, byte for byte (the door hashes its own copy) |
| 409 | `stale_state` | an earlier artifact it read is not the droplet's, is no longer the newest, or is newer than the export |

What the door cannot check, plainly: that each warehouse read's hash is what that export gives for that query.
That would mean running the product on the droplet, which is what a burst avoids, or running SQL taken from the
upload, which it never does. It rests on your machine (basic auth plus the key, deployed code), and
`tools/regenerate.py` re-checks it afterwards from the export the manifest names.

## Rotating or closing the door

Change the key in both places (`/etc/ohcamel/burst.env`, then `docker compose -f deploy/docker-compose.yml
--profile live up -d ohcamel-burst` on the droplet; your environment). To close it, empty the file and restart
the service the same way: every request is then `503 burst_off`.
