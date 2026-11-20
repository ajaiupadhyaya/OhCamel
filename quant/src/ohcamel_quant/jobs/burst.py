"""Burst research (completion task 3.7, spec ruling R6): a heavy job runs on another machine against last night's
warehouse export and publishes its artifact through the burst door on the live host.

    make research-burst KIND=<kind> PARAMS='<json>' [DRY_RUN=1]
    tools/burst.py --kind KIND [--params JSON] [--dry-run] [--export DIR | --no-pull] [--threads N] [--keep]

R6 keeps the droplet its size; heavy research runs elsewhere. In order (``main``):

1. The kind must be ``burstable`` (jobs/kinds.py: farm.sweep, models.xs_lgbm, cov.league, risk.mc_atlas) and this
   checkout clean -- the manifest's ``code_sha`` is the checkout's HEAD, and a dirty tree would make it a lie.
2. The export: ``deploy/pull-backups.sh`` (the owner's existing rsync over the ``ssh ohcamel`` alias) brings the
   droplet's newest export to ``~/Backups/ohcamel/warehouse/DATE``; the newest whole one is used (``--export DIR``
   names one and skips the pull).
3. The hold (not in a dry run): ``POST /burst/hold`` on the live host holds the queued job with this kind and params
   -- or creates one the worker never runs -- so the worker does not run it meanwhile, and returns the droplet's
   code SHA and lock hash (a mismatch stops here, before an hour of compute) and the earlier artifacts the run may
   read, which are mirrored into the scratch directory (``GET /burst/artifact/...``): the farm carries its cells
   forward, Q01 its frozen selection and once-only holdout, and a run that did not see the droplet's newest would
   score a holdout twice. The door re-checks every such read against its own store.
4. The run: the export is restored into a NEW DuckDB file in a scratch directory (``restore_export``, the code
   deploy/restore.sh and the drill use), and the handler runs here exactly as the worker's child runs it
   (``local.execute``: the recorder, the seed check, the tables), offline, with ``OHCAMEL_THREADS`` and the BLAS
   thread variables set to this machine's cores (tools/burst.py sets them before numpy loads), its data directory
   in the scratch, and the warehouse freshness clock pinned at the export's ``exported_at`` -- the moment the data
   is from, so the facade serves what the worker would have served right after the export. That clock is the
   manifest's ``started_at`` (tools/regenerate.py replays it); when the run really happened is ``burst.ran_at``.
5. The manifest is ``artifacts.build_manifest``'s, unchanged, plus ``host: burst`` and a ``burst`` block: the
   export (day, digest, exported_at), when the run started and finished, the machine's platform and cores.
   ``runtime.platform`` and ``kernels`` say where it ran: macOS and Linux differ by up to ~6e-7 on an
   optimizer-fitted product, so a burst artifact is reproducible exactly only on its own platform.
6. The upload (not in a dry run): ``POST /burst/jobs/<id>/artifact``, the manifest and every file in one JSON body,
   behind the live host's basic auth and signed (below). A retried upload the door already published counts as
   success.

A dry run (``--dry-run`` / ``DRY_RUN=1``) does steps 1, 2, 4 and 5 and stops: nothing is held or uploaded and the
droplet is not contacted beyond the rsync pull (none at all with ``--export``). Earlier artifacts are not fetched
in a dry run, so a farm or Q01 dry run starts from no state; it says so.

SIGNING. The spec does not say what signs an upload, so: HMAC-SHA256, keyed by ``OHCAMEL_BURST_SECRET`` (at least
32 characters; the door refuses everything while its copy is unset or shorter), over
``ohcamel-burst/1 \\n METHOD \\n PATH \\n TS \\n SHA-256(body)``. The body holds the manifest and every file, so the MAC
covers the manifest and each file's bytes. TS is Unix seconds, sent in ``X-OhCamel-Burst-Ts``; the door refuses
one more than five minutes from its clock. The MAC is ``X-OhCamel-Burst-Signature``, compared in constant time.
The path binds a signature to one job; a job is published once, so a replay finds it done.

THE OWNER'S ENVIRONMENT, never the repository, never printed:
    OHCAMEL_BURST_URL     https://<the live host>
    OHCAMEL_BURST_AUTH    user:password of the live host's basic auth
    OHCAMEL_BURST_SECRET  the door's HMAC key (the droplet holds the same value in /etc/ohcamel/burst.env)
    OHCAMEL_PULL_DIR      where pull-backups.sh puts the copies (default ~/Backups/ohcamel)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SECRET_ENV = "OHCAMEL_BURST_SECRET"
AUTH_ENV = "OHCAMEL_BURST_AUTH"
URL_ENV = "OHCAMEL_BURST_URL"
MIN_SECRET_LEN = 32
TS_HEADER = "X-OhCamel-Burst-Ts"
SIG_HEADER = "X-OhCamel-Burst-Signature"
TS_WINDOW_S = 300
#: The largest upload the door reads (Caddy's request_body cap on the live host is the same).
MAX_UPLOAD_BYTES = 64 * 2**20
READS_FILE = "reads.json"
HOST = "burst"

#: Per burstable kind, the only tables it may upload -- the tables its product can return (products/atlas.py,
#: cov_league.py, farm.py, q01.py). A file name is taken from this list, never from the upload.
BURST_TABLES: dict[str, frozenset[str]] = {
    "risk.mc_atlas": frozenset({"summary", "euler", "members"}),
    "cov.league": frozenset({"league", "tests", "skipped"}),
    "farm.sweep": frozenset({"cells", "regimes", "trials", "leaderboard"}),
    "models.xs_lgbm": frozenset({"gates", "selection", "trials", "stitched", "holdout", "holdout_returns",
                                 "holdout_costs", "holdout_regimes", "holdout_ic", "holdout_deciles", "model",
                                 "scores"}),
}
#: Per burstable kind, the kinds whose earlier artifacts its product reads (products.io lookups), mirrored from the
#: droplet before the run. The door checks every artifact read whatever this says.
LOOKUP_KINDS: dict[str, tuple[str, ...]] = {
    "risk.mc_atlas": (), "cov.league": (), "farm.sweep": ("farm.sweep",), "models.xs_lgbm": ("models.xs_lgbm",),
}
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_FILE = re.compile(r"^(manifest\.json|[a-z][a-z0-9_]{0,63}\.parquet)$")


def fixture_reads(reads: list[dict[str, Any]]) -> list[str]:
    """Reads served from the committed fixtures rather than the export. The worker is online, so a stale warehouse
    sends it to the vendors; a burst run is offline, so the facade would fall back to the fixtures (ten years
    ending 2026-06-01) -- real, but not the export's data. A burst artifact must not carry them; tool and door both
    refuse it."""
    return [str(r.get("name"))[:120] for r in reads
            if any(part.strip().startswith("fixture") for part in str(r.get("source")).split(","))]


class BurstError(RuntimeError):
    """Something the tool refuses, or the door refused; the message says which and why."""


def usable_secret(value: str | None) -> str | None:
    return value if isinstance(value, str) and len(value) >= MIN_SECRET_LEN else None


def signature(secret: str, method: str, path: str, ts: int, body: bytes) -> str:
    msg = "\n".join(("ohcamel-burst/1", method.upper(), path, str(int(ts)), hashlib.sha256(body).hexdigest()))
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def signed_headers(secret: str, method: str, path: str, body: bytes, *, now: datetime | None = None) -> dict[str, str]:
    ts = int((now or datetime.now(UTC)).timestamp())
    return {TS_HEADER: str(ts), SIG_HEADER: signature(secret, method, path, ts, body),
            "Content-Type": "application/json"}


# --- the run --------------------------------------------------------------------------------------------------------

@dataclass
class BurstRun:
    kind: str
    params: dict[str, Any]
    result: dict[str, Any]          # local.execute's result: the recorded reads, seeds, kernels, runtime, tables
    staging: Path                   # the tables, one Parquet each
    export: dict[str, str]          # day, digest, exported_at
    clock: str                      # the pinned clock (the export's exported_at), the manifest's started_at
    ran_at: str
    finished_at: str
    cpu_seconds: float
    peak_rss_bytes: int | None
    threads: int

    @property
    def result_data_hash(self) -> str:
        from .provenance import data_hash

        return data_hash(self.result["reads"])


_SETTINGS_ENV = ("OHCAMEL_QUANT_DATA_DIR", "OHCAMEL_QUANT_WAREHOUSE_PATH", "OHCAMEL_QUANT_OFFLINE",
                 "OHCAMEL_QUANT_SIGNALS_DIR")


def run_burst(kind: str, params: dict[str, Any], export_dir: Path, *, scratch: Path, threads: int,
              data_dir: Path | None = None) -> BurstRun:
    """Restore ``export_dir`` into ``scratch`` and run ``kind`` here on it (module docstring, step 4). ``data_dir``
    holds the mirrored earlier artifacts (default ``scratch/data``, empty). Nothing outside ``scratch`` and
    ``data_dir`` is written: the process's settings point there for the run and are put back after."""
    import resource

    from ..config import Settings, get_settings
    from ..warehouse.export import export_digest, read_record, restore_export
    from .db import iso, parse_iso
    from .kinds import get_kind
    from .local import execute, make_context, pinned_clock
    from .rusage import maxrss_bytes

    if not get_kind(kind).burstable:
        raise BurstError(f"{kind} is not burstable")
    export_dir, scratch = Path(export_dir), Path(scratch)
    record = read_record(export_dir)
    export = {"day": str(record["day"]), "digest": export_digest(export_dir), "exported_at": str(record["exported_at"])}
    clock = parse_iso(export["exported_at"])
    scratch.mkdir(parents=True, exist_ok=True)
    wh = scratch / "warehouse.duckdb"
    if wh.exists():
        raise BurstError(f"{wh} exists: a burst restores into a new scratch directory")
    restore_export(export_dir, wh)
    data = Path(data_dir) if data_dir is not None else scratch / "data"
    data.mkdir(parents=True, exist_ok=True)
    staging = scratch / "out"
    shutil.rmtree(staging, ignore_errors=True)
    saved = {k: os.environ.get(k) for k in _SETTINGS_ENV}
    # products look earlier artifacts up through the process's settings (jobs_db_path()), not the context's: point
    # those at the scratch too, so a run never reads this machine's own artifacts
    os.environ.update({"OHCAMEL_QUANT_DATA_DIR": str(data), "OHCAMEL_QUANT_WAREHOUSE_PATH": str(wh),
                       "OHCAMEL_QUANT_OFFLINE": "1", "OHCAMEL_QUANT_SIGNALS_DIR": ""})
    get_settings.cache_clear()
    settings = Settings(warehouse_path=wh, offline=True, data_dir=data, signals_dir=None)
    ctx = make_context(dict(params), settings=settings, threads=int(threads), job_id="burst")
    before = resource.getrusage(resource.RUSAGE_SELF)
    ran_at = datetime.now(UTC)
    try:
        with pinned_clock(clock):
            result = execute(kind, dict(params), ctx, staging)
    finally:
        ctx.close()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()
    after = resource.getrusage(resource.RUSAGE_SELF)
    return BurstRun(kind=kind, params=dict(params), result=result, staging=staging, export=export, clock=iso(clock),
                    ran_at=iso(ran_at), finished_at=iso(datetime.now(UTC)),
                    cpu_seconds=round((after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime), 3),
                    peak_rss_bytes=maxrss_bytes(after.ru_maxrss), threads=int(threads))


def manifest_for(run: BurstRun, *, job_id: str, code_sha: str) -> dict[str, Any]:
    """The worker's manifest (``build_manifest``, every check it makes) for ``job_id``, plus ``host: burst`` and the
    ``burst`` block. ``reads`` is the full list here; the upload sends it as reads.json."""
    from .artifacts import build_manifest
    from .queue import params_hash

    job = SimpleNamespace(id=job_id, kind=run.kind, params=run.params, params_hash=params_hash(run.params),
                          started_at=run.clock)
    m = build_manifest(job, run.result, code_sha=code_sha, finished_at=run.finished_at,  # type: ignore[arg-type]
                       cpu_seconds=run.cpu_seconds, peak_rss_bytes=run.peak_rss_bytes)
    m["host"] = HOST
    m["burst"] = {"export": dict(run.export), "ran_at": run.ran_at, "finished_at": run.finished_at,
                  "machine": {"platform": str((run.result.get("runtime") or {}).get("platform")),
                              "cores": int(os.cpu_count() or 1)}}
    return m


def body_for(run: BurstRun, manifest: dict[str, Any]) -> bytes:
    """The upload: ``{"manifest": ..., "files": {name: base64}}`` -- each table's Parquet bytes and reads.json."""
    m = dict(manifest)
    reads = m.pop("reads", [])
    files = {f"{t}.parquet": base64.b64encode((run.staging / f"{t}.parquet").read_bytes()).decode()
             for t in m.get("tables") or [] if (run.staging / f"{t}.parquet").is_file()}
    files[READS_FILE] = base64.b64encode(json.dumps(reads, separators=(",", ":")).encode()).decode()
    return json.dumps({"manifest": m, "files": files}, separators=(",", ":")).encode()


# --- the door, from this side ---------------------------------------------------------------------------------------

def _http_client(base_url: str, auth: tuple[str, str] | None) -> Any:
    import httpx

    return httpx.Client(base_url=base_url, auth=auth, timeout=httpx.Timeout(30.0, read=600.0, write=600.0))


def _refused(r: Any) -> BurstError:
    try:
        j = r.json()
        why = f"{j.get('error')}: {j.get('detail')}"
    except ValueError:
        why = (r.text or "")[:300]
    return BurstError(f"the burst door refused it (HTTP {r.status_code}) -- {why}")


def hold(kind: str, params: dict[str, Any], *, client: Any, base_url: str, secret: str,
         auth: tuple[str, str] | None) -> dict[str, Any]:
    path = "/burst/hold"
    body = json.dumps({"kind": kind, "params": params}, separators=(",", ":")).encode()
    r = client.post(base_url + path, content=body, headers=signed_headers(secret, "POST", path, body), auth=auth)
    if r.status_code != 200:
        raise _refused(r)
    return r.json()


def mirror(previous: list[dict[str, Any]], data_dir: Path, *, client: Any, base_url: str, secret: str,
           auth: tuple[str, str] | None) -> int:
    """Copy the droplet's earlier artifacts the door listed into ``data_dir`` (jobs.sqlite rows + files), so the
    run's lookups find what the worker's would. Every name is checked here too before it becomes a path."""
    from contextlib import closing

    from .db import connect
    from .kinds import REGISTRY

    data_dir = Path(data_dir)
    root = data_dir / "artifacts"
    n = 0
    with closing(connect(data_dir / "jobs.sqlite")) as conn:
        for p in previous:
            kind, aid = str(p["kind"]), str(p["id"])
            if kind not in REGISTRY or not _ULID.match(aid) or not all(_FILE.match(str(f)) for f in p["files"]):
                raise BurstError(f"the door listed an artifact this tool will not write: {kind}/{aid}")
            d = root / kind / aid
            d.mkdir(parents=True, exist_ok=True)
            for name in p["files"]:
                path = f"/burst/artifact/{kind}/{aid}/{name}"
                r = client.get(base_url + path, headers=signed_headers(secret, "GET", path, b""), auth=auth)
                if r.status_code != 200:
                    raise _refused(r)
                (d / str(name)).write_bytes(r.content)
            conn.execute("INSERT OR REPLACE INTO artifacts (id, kind, params_hash, finished_at, path, data_asof)"
                         " VALUES (?,?,?,?,?,?)", (aid, kind, str(p["params_hash"]), str(p["finished_at"]), str(d),
                                                    p.get("data_asof")))
            n += 1
    return n


def upload(run: BurstRun, job_id: str, *, client: Any, base_url: str, secret: str, auth: tuple[str, str] | None,
           code_sha: str | None = None) -> dict[str, Any]:
    from .provenance import checkout_sha

    m = manifest_for(run, job_id=job_id, code_sha=code_sha or checkout_sha())
    body = body_for(run, m)
    path = f"/burst/jobs/{job_id}/artifact"
    r = client.post(base_url + path, content=body, headers=signed_headers(secret, "POST", path, body), auth=auth)
    if r.status_code == 201:
        return r.json()
    if r.status_code == 409:
        j = r.json()
        pub = j.get("published") or {}
        if j.get("error") == "already_done" and pub.get("host") == HOST and pub.get("data_hash") == m["data_hash"]:
            return {"id": job_id, "already": True, "data_hash": m["data_hash"]}
    raise _refused(r)


# --- the command ----------------------------------------------------------------------------------------------------

def _say(msg: str) -> None:
    print(f"burst: {msg}", flush=True)


def _tree_clean() -> bool:
    from ..config import REPO_ROOT

    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"], capture_output=True, text=True,
                             check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return out.strip() == ""


def newest_export(pull_dir: Path) -> Path:
    from ..warehouse.export import ExportFailed, read_record

    root = Path(pull_dir) / "warehouse"
    days = sorted((p for p in root.glob("*") if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)),
                  key=lambda p: p.name, reverse=True)
    for d in days:
        try:
            read_record(d)
        except ExportFailed:
            continue
        return d
    raise BurstError(f"no verified export under {root} (run deploy/pull-backups.sh, or pass --export DIR)")


def _pull() -> None:
    from ..config import REPO_ROOT

    script = REPO_ROOT / "deploy" / "pull-backups.sh"
    _say(f"pulling the droplet's newest export ({script.relative_to(REPO_ROOT)})")
    if subprocess.run([str(script)], check=False).returncode != 0:
        raise BurstError("deploy/pull-backups.sh failed; nothing was run (its output says why)")


def _credentials() -> tuple[str, str, tuple[str, str]]:
    url = os.environ.get(URL_ENV, "").strip().rstrip("/")
    secret = usable_secret(os.environ.get(SECRET_ENV))
    auth = os.environ.get(AUTH_ENV, "")
    missing = [n for n, ok in ((URL_ENV, url.startswith("https://")), (SECRET_ENV, secret is not None),
                               (AUTH_ENV, ":" in auth)) if not ok]
    if missing:
        raise BurstError("set " + ", ".join(missing) + " in your environment (docs/runbooks/burst.md); the URL must be "
                         f"https://, the secret at least {MIN_SECRET_LEN} characters, the auth user:password")
    user, _, password = auth.partition(":")
    assert secret is not None
    return url, secret, (user, password)


def main(argv: list[str] | None = None) -> int:
    from ..config import REPO_ROOT

    p = argparse.ArgumentParser(prog="tools/burst.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--kind", required=True)
    p.add_argument("--params", default="{}", help="the job's params, a JSON object (the queued job's, exactly)")
    p.add_argument("--dry-run", action="store_true", help="run here and stop: nothing is held or uploaded")
    p.add_argument("--export", type=Path, default=None, help="use this export directory; no pull")
    p.add_argument("--no-pull", action="store_true", help="use the newest export already pulled")
    p.add_argument("--pull-dir", type=Path, default=Path(os.environ.get("OHCAMEL_PULL_DIR")
                                                         or Path.home() / "Backups" / "ohcamel"))
    p.add_argument("--threads", type=int, default=int(os.environ.get("OHCAMEL_THREADS") or os.cpu_count() or 1))
    p.add_argument("--scratch", type=Path, default=None, help="work here (default: a new temporary directory)")
    p.add_argument("--keep", action="store_true", help="keep the scratch directory")
    a = p.parse_args(argv)
    from .kinds import REGISTRY
    from .provenance import checkout_sha, lock_hash

    spec = REGISTRY.get(a.kind)
    if spec is None or not spec.burstable:
        print(f"burst: REFUSED -- {a.kind} is not burstable (burstable: "
              f"{', '.join(sorted(k for k, s in REGISTRY.items() if s.burstable))})", file=sys.stderr)
        return 2
    try:
        params = json.loads(a.params)
        if not isinstance(params, dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        print(f"burst: REFUSED -- --params must be a JSON object ({e})", file=sys.stderr)
        return 2
    own = a.scratch is None
    scratch = Path(tempfile.mkdtemp(prefix="ohcamel-burst-")) if own else Path(a.scratch)
    try:
        if not _tree_clean():
            if not a.dry_run:
                raise BurstError(f"{REPO_ROOT} has uncommitted changes: the manifest's code_sha would not be the code "
                                 "that ran. Commit or stash, check out the commit the droplet runs, and burst again")
            _say("WARNING the checkout has uncommitted changes (a real run refuses this)")
        if a.export is not None:
            export = a.export
        else:
            if not a.no_pull:
                _pull()
            export = newest_export(a.pull_dir)
        _say(f"{a.kind} {json.dumps(params, sort_keys=True)} on export {export} ({a.threads} threads)")
        data = scratch / "data"
        job_id = "burst-dry-run"
        client = url = secret = auth = None
        if not a.dry_run:
            url, secret, auth = _credentials()
            client = _http_client(url, auth)
            held = hold(a.kind, params, client=client, base_url="", secret=secret, auth=auth)
            job_id = held["job_id"]
            _say(f"job {job_id} held until {held['hold_until']}" + (" (created for this burst)" if held["created"]
                                                                    else ""))
            here = checkout_sha()
            if held.get("code_sha") != here or held.get("lock_hash") != lock_hash():
                raise BurstError(f"the droplet runs code {held.get('code_sha')} (lock {str(held.get('lock_hash'))[:12]}); "
                                 f"this checkout is {here} (lock {str(lock_hash())[:12]}). Check out the droplet's "
                                 "commit and burst again; the door refuses an artifact other code made")
            if held.get("previous"):
                n = mirror(held["previous"], data, client=client, base_url="", secret=secret, auth=auth)
                _say(f"mirrored {n} earlier artifact(s) the run may read from the droplet")
        elif LOOKUP_KINDS[a.kind]:
            _say(f"dry run: the droplet's earlier {', '.join(LOOKUP_KINDS[a.kind])} artifacts are not fetched, so "
                 "this run starts from no carried state (a real run mirrors them first)")
        run = run_burst(a.kind, params, export, scratch=scratch, threads=a.threads, data_dir=data)
        stale = fixture_reads(run.result["reads"])
        if stale:
            raise BurstError("the run read the committed fixtures, not the export (the warehouse was not fresh at the "
                             f"export's moment, {run.clock}): " + "; ".join(stale[:5]) + " -- the door refuses this; "
                             "use an export taken after a complete ingest")
        m = manifest_for(run, job_id=job_id, code_sha=checkout_sha())
        body = body_for(run, m)
        _say(f"ran in {run.cpu_seconds:.1f} cpu s on {m['burst']['machine']['platform']} (kernels "
             f"{(m.get('kernels') or {}).get('engine')}); verdict {m.get('verdict')}; data_hash {m['data_hash']}; "
             f"tables {', '.join(m['tables'])}; upload {len(body) / 2**20:.2f} MiB")
        if len(body) > MAX_UPLOAD_BYTES:
            raise BurstError(f"the upload is {len(body)} bytes, over the door's {MAX_UPLOAD_BYTES}")
        if a.dry_run:
            _say("dry run: stopped before the droplet -- nothing was held or uploaded")
            return 0
        out = upload(run, job_id, client=client, base_url="", secret=secret, auth=auth)  # type: ignore[arg-type]
        _say(f"published {a.kind}/{out['id']} as a burst artifact" + (" (an earlier upload already had)"
                                                                       if out.get("already") else ""))
        return 0
    except BurstError as e:
        print(f"burst: REFUSED -- {e}", file=sys.stderr, flush=True)
        return 1
    finally:
        if own and not a.keep:
            shutil.rmtree(scratch, ignore_errors=True)
        elif a.keep or not own:
            _say(f"scratch kept at {scratch}")


if __name__ == "__main__":
    raise SystemExit(main())
