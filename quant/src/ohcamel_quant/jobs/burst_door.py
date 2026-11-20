"""The burst door (completion task 3.7, spec R6): the live host's one write path for an artifact made elsewhere.

It runs as its own service (``ohcamel-quant burst-door``, compose service ohcamel-burst, api/burst_app.py), reached
only through the live host's Caddy block, behind its basic auth. The public app does not mount it: the public host
has no route to it at all. Three requests, each signed (jobs/burst.py, SIGNING):

* ``POST /burst/hold {kind, params}`` creates, or renews, the burst job with that kind and params
  (``submitted_by='burst'``; the worker never runs it) for the kind's wall limit plus ``HOLD_MARGIN_S`` (at most
  ``MAX_HOLD_S``), and answers with the job id, the droplet's code SHA and lock hash, the exports it holds, and the
  earlier artifacts the run may read (``previous_for``). 403 for a kind not marked burstable; 409 ``scheduled``
  while a schedule's job with the same kind and params is live -- a burst never changes whether, when or as which
  job the worker runs a scheduled one (fix round 1, I1: a schedule firing during a burst gets its own job).
* ``GET /burst/previous/<kind>`` is that list without a hold: a dry run mirrors the droplet's state too.
* ``GET /burst/artifact/<kind>/<id>/<file>`` serves one file of one of those earlier artifacts, byte for byte:
  ``manifest.json`` or a table its manifest lists, of a kind some burstable kind reads, under the artifact root.
* ``POST /burst/jobs/<id>/artifact`` is the upload. ``accept_upload`` refuses, in order:

  ====  =========================  ===============================================================================
  503   burst_off                  the door's secret is unset or under 32 characters (every request)
  413   too_large                  a body over ``burst.MAX_UPLOAD_BYTES`` (every request)
  401   bad_signature / stale      no MAC or nonce, a wrong one, or a timestamp more than 5 minutes off
  409   replayed                   a POST whose signature the door already received inside the window
  429   busy                       another upload is being verified (uploads are one at a time)
  422   bad_request                not the JSON ``{"manifest": {...}, "files": {...}}``
  404   not_found                  no such job
  409   scheduled                  the job is not a burst job: a schedule queued it and the worker runs it
  403   not_burstable              the job's kind, or the manifest's, is not burstable
  409   claimed                    the worker is running the job; its own artifact will be published
  409   already_done               the job is done (the body names the artifact, so a retried upload can tell)
  409   not_queued                 the job failed or was cancelled
  409   job_mismatch               the manifest's kind, params or params_hash are not the queued job's
  422   bad_manifest               a field publish does not know, a missing one, a wrong type, a bad verdict or
                                   engine, an unseeded draw (``build_manifest``'s own checks), host not "burst"
  422   bad_file                   a file name outside the kind's allow-list (``burst.BURST_TABLES``) or a table
                                   missing; base64 that is not; bytes that are not a readable Parquet file, or
                                   one that would decode past the budget (rows, columns, cells, bytes: M2); a
                                   bad reads.json
  422   inconsistent_manifest      the manifest the door rebuilds with ``build_manifest`` from the job, the reads
                                   and the uploaded fields differs from the uploaded one -- the data_hash the door
                                   computes from reads.json among them
  422   unrecorded_reads           a null data_hash: a burst artifact's whole claim is what it read
  409   not_from_export            a read from a source no export holds (a vendor, the live engine), or one the
                                   facade served from the committed fixtures because the warehouse was not fresh
  409   code_drift                 other code: the manifest's code SHA or lock hash is not this deployment's
  409   no_such_export             the export it names is not one the droplet holds: no such day, or the digest
                                   the door computes over the files it holds differs, or its exported_at does
  422   clock                      started_at is not the export's exported_at (the run's clock must be the data's)
  409   stale_state                an earlier artifact it read is not what the droplet holds, is no longer the
                                   newest, or was published after the export (``verify_artifact_reads``)
  409   holdout_worker_only        an EXP-Q01 upload carries holdout tables that are not the droplet's newest
                                   holdout, unchanged: a burst never evaluates the once-only holdout (I2)
  422   schema_mismatch            a table's column names or types are not those of the droplet's newest worker
                                   artifact of the kind (an all-null column matches any type; M3)
  ====  =========================  ===============================================================================

  Then the files are written into a fresh staging directory under names from the kind's allow-list and
  ``queue.publish_burst`` publishes them in one transaction (still queued, same kind and params, or 409). Nothing
  uploaded is executed, imported, unpickled or used as a path: the JSON is parsed, the Parquet is read by pyarrow
  from memory, the reads are compared, and the files are written as bytes.

WHAT THE DOOR CANNOT CHECK, PLAINLY. It cannot recompute ``data_hash`` from the export: that means running the
product (which is what a burst exists not to do on this host) or executing the SQL in each read's name (never: it
came from the upload). What it does compute itself: the export's digest over the files it holds (so the export is
one it has, byte for byte), the data_hash over the uploaded reads (so the manifest names exactly those reads), that
every read is of a source an export holds, and every earlier-artifact read against its own store. That each
warehouse read's hash is what that export returns for that query rests on the uploader -- the owner's machine,
authenticated by the basic auth and the MAC, running the deployed code (code_drift) -- and is checkable afterwards:
``tools/regenerate.py <artifact> --warehouse <the export the burst block names>`` reruns it. Likewise the gates the
worker's child applies while the handler runs: the door passes ``unseeded=[]`` and ``unrecorded=[]`` to
``build_manifest`` because it cannot see the run, so the unseeded-randomness check (the recorder watching numpy and
``random``) and every gate the handler computes inside itself (its verdict) rest on the client running the deployed
code; regenerate re-runs them. docs/runbooks/burst.md has the owner regenerate every burst artifact on the droplet.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import burst as B
from .artifacts import TABLE_NAME, inside
from .db import connect, parse_iso
from .queue import publish_burst

#: How long past the kind's wall limit a hold lasts: the restore before the run and the upload after it.
HOLD_MARGIN_S = 3600
#: The longest hold the door grants (fix round 1, M6): a budget past it is refused, not turned into a timedelta
#: overflow. Two days is the farm's 90-minute budget many times over.
MAX_HOLD_S = 2 * 86400
MAX_MANIFEST_BYTES = 1 << 20
MAX_PARAMS_BYTES = 16_384
#: Nesting the door parses (fix round 1, M6): deeper JSON is refused before anything recurses over it.
MAX_JSON_DEPTH = 32
#: A timestamp header is Unix seconds: at most 12 digits (M6: thousands of digits are refused, not converted).
MAX_TS_DIGITS = 12

# The decode budget (fix round 1, M2), sized to ohcamel-burst's 384 MiB limit. Peak while an upload is verified:
#   ~130 MiB  the process (Python, FastAPI, pandas and pyarrow imported)
#   +64 MiB   the request body (the cap), dropped once parsed
#   +64 MiB   the parsed JSON's base64 text, each file's text dropped as it is decoded
#   +48 MiB   the decoded files (3/4 of the base64), replacing that text
#   +<=16 MiB one Parquet record batch decoded at a time (1,024 rows; string columns read as dictionaries), freed
#             before the next
# The body and the text are not held together with all the decoded files, so the peak is under ~310 MiB, and
# uploads are verified one at a time (_UPLOAD_LOCK; a second gets 429) so two cannot double it. Per upload, across
# its tables: at most MAX_DECODED_BYTES decoded (summed batch by batch), MAX_ENCODED_BYTES of uncompressed column
# chunks (Parquet metadata, before decoding), MAX_CELLS rows x columns; per table at most MAX_ROWS rows and
# MAX_COLUMNS columns.
MAX_ROWS = 2_000_000
MAX_COLUMNS = 512
MAX_CELLS = 8_000_000
MAX_ENCODED_BYTES = 64 * 2**20
MAX_DECODED_BYTES = 96 * 2**20
BATCH_ROWS = 1024
_UPLOAD_LOCK = threading.Lock()
#: Signatures of POSTs seen inside the timestamp window (fix round 1, M5): a replayed hold or upload is refused.
_SEEN: dict[str, float] = {}
_SEEN_LOCK = threading.Lock()
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KIND = r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+"
_LOOKUP = re.compile(rf"^artifact-lookup: (?P<kind>{_KIND}) params_hash=(?P<ph>None|[0-9a-f]{{64}}) "
                     r"with_table=(?P<t>None|[a-z][a-z0-9_]{0,63})(?P<wo> worker_only=True)?$")
_TABLE_READ = re.compile(rf"^artifact: (?P<kind>{_KIND})/(?P<id>[0-9A-HJKMNP-TV-Z]{{26}})/(?P<t>[a-z][a-z0-9_]{{0,63}})$")
_READ_KEYS = {"name", "sha256", "source", "regenerable"}


class Refused(Exception):
    def __init__(self, status: int, error: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.status, self.error, self.detail, self.extra = status, error, detail, extra

    def body(self) -> dict[str, Any]:
        return {"error": self.error, "detail": self.detail, **self.extra}


@dataclass
class Door:
    db: Path              # jobs.sqlite
    root: Path            # the artifact root
    exports: Path         # the nightly exports the droplet holds (/var/backups/ohcamel/warehouse, read-only)
    secret: str | None
    code_sha: str         # this deployment's build
    lock_hash: str | None

    @classmethod
    def from_env(cls) -> Door:
        from .paths import artifacts_root, jobs_db_path
        from .provenance import checkout_sha, lock_hash

        return cls(db=jobs_db_path(), root=artifacts_root(), exports=Path(os.environ.get("OHCAMEL_BURST_EXPORTS",
                                                                                         "/exports")),
                   secret=os.environ.get(B.SECRET_ENV), code_sha=checkout_sha(), lock_hash=lock_hash())


# --- every request --------------------------------------------------------------------------------------------------

def check_request(door: Door, method: str, path: str, headers: Any, body: bytes, *, now: datetime | None = None,
                  single_use: bool = False) -> None:
    """The secret, the size, the timestamp and the MAC; ``single_use`` (every POST) also refuses a signature already
    seen inside the window, so a captured hold or upload cannot be replayed (409 replayed)."""
    secret = B.usable_secret(door.secret)
    if secret is None:
        raise Refused(503, "burst_off", f"the burst door is off: {B.SECRET_ENV} is not set (at least "
                                        f"{B.MIN_SECRET_LEN} characters) on this host")
    if len(body) > B.MAX_UPLOAD_BYTES:
        raise Refused(413, "too_large", f"at most {B.MAX_UPLOAD_BYTES} bytes")
    ts_text, sig = headers.get(B.TS_HEADER) or "", headers.get(B.SIG_HEADER) or ""
    nonce = headers.get(B.NONCE_HEADER) or ""
    if not ts_text.isdigit() or len(ts_text) > MAX_TS_DIGITS or not re.fullmatch(r"[0-9a-f]{64}", sig) \
            or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise Refused(401, "bad_signature", "the request is not signed (docs/runbooks/burst.md)")
    t = (now or datetime.now(UTC)).timestamp()
    if abs(t - int(ts_text)) > B.TS_WINDOW_S:
        raise Refused(401, "stale", f"the request's timestamp is more than {B.TS_WINDOW_S} s from this host's clock")
    want = B.signature(secret, method, path, int(ts_text), body, nonce)
    if not hmac.compare_digest(want, sig):
        raise Refused(401, "bad_signature", "the signature does not match")
    if single_use:
        with _SEEN_LOCK:
            mono = time.monotonic()
            for k in [k for k, exp in _SEEN.items() if exp < mono]:
                del _SEEN[k]
            if sig in _SEEN:
                raise Refused(409, "replayed", "this signed request was already received; sign a new one")
            _SEEN[sig] = mono + 2 * B.TS_WINDOW_S + 1


def _depth_ok(obj: Any, limit: int = MAX_JSON_DEPTH) -> bool:
    """True when ``obj`` nests no deeper than ``limit`` (iterative: nothing here recurses)."""
    stack = [(obj, 1)]
    while stack:
        o, d = stack.pop()
        if isinstance(o, (dict, list)):
            if d > limit:
                return False
            stack.extend((v, d + 1) for v in (o.values() if isinstance(o, dict) else o))
    return True


def _json(body: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise Refused(422, "bad_request", f"the body is not JSON the door reads ({type(e).__name__})") from e
    if not isinstance(obj, dict):
        raise Refused(422, "bad_request", "the body is not a JSON object")
    if not _depth_ok(obj):
        raise Refused(422, "bad_request", f"the body nests deeper than {MAX_JSON_DEPTH} levels")
    return obj


def _burstable(kind: Any) -> Any:
    from .kinds import REGISTRY

    spec = REGISTRY.get(kind) if isinstance(kind, str) else None
    if spec is None or not spec.burstable:
        raise Refused(403, "not_burstable", f"{kind!r} is not a burstable kind (jobs/kinds.py)")
    return spec


# --- hold -----------------------------------------------------------------------------------------------------------

def hold(door: Door, body: bytes, *, now: datetime | None = None) -> dict[str, Any]:
    from .kinds import wall_limit_for
    from .queue import canonical_params, hold_for_burst

    now = now or datetime.now(UTC)
    obj = _json(body)
    if set(obj) != {"kind", "params"} or not isinstance(obj["params"], dict):
        raise Refused(422, "bad_request", 'the body is {"kind": ..., "params": {...}}')
    spec = _burstable(obj["kind"])
    try:
        if len(canonical_params(obj["params"])) > MAX_PARAMS_BYTES:
            raise Refused(422, "bad_request", f"params are at most {MAX_PARAMS_BYTES} bytes")
    except ValueError as e:
        raise Refused(422, "bad_request", str(e)) from e
    hold_s = wall_limit_for(spec, obj["params"]) + HOLD_MARGIN_S
    if not hold_s <= MAX_HOLD_S:
        raise Refused(422, "bad_request", f"the job's wall limit would hold it past {MAX_HOLD_S} s; a burst job's "
                                          "budget must be smaller")
    with closing(connect(door.db)) as conn:
        job, created, until = hold_for_burst(conn, kind=spec.name, params=obj["params"], mem_class=spec.mem_class,
                                             heavy=spec.heavy, hold_s=hold_s, now=now)
        if until is None:
            if job.submitted_by != "burst":
                raise Refused(409, "scheduled", f"{spec.name} {job.id} with these params was queued by "
                                                f"{job.submitted_by} and is {job.state}: the worker runs it and "
                                                "publishes its artifact; a burst never stands in for it")
            raise Refused(409, "claimed", f"{spec.name} {job.id} is {job.state}")
        previous = previous_for(conn, door.root, spec.name)
    return {"job_id": job.id, "created": created, "hold_until": until, "code_sha": door.code_sha,
            "lock_hash": door.lock_hash, "previous": previous, "exports": held_exports(door.exports)}


def held_exports(root: Path) -> list[dict[str, Any]]:
    from ..warehouse.export import ExportFailed, read_record

    out = []
    if Path(root).is_dir():
        for d in sorted(Path(root).iterdir()):
            if d.is_dir() and not d.is_symlink() and _DAY.match(d.name):
                try:
                    out.append({"day": d.name, "exported_at": read_record(d).get("exported_at")})
                except ExportFailed:
                    continue
    return out


def previous_for(conn: Any, root: Path, kind: str) -> list[dict[str, Any]]:
    """The earlier artifacts a run of ``kind`` may read: for each kind it looks up (``burst.LOOKUP_KINDS``), the
    newest artifact, the newest worker artifact, and the newest (and newest worker) one holding each table -- every
    answer ``products.io.previous_artifact`` can give with no params_hash, with or without ``worker_only``."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for k in B.LOOKUP_KINDS.get(kind, ()):
        need_any = need_worker = True
        t_any: set[str] = set()
        t_worker: set[str] = set()
        for r in conn.execute("SELECT id, kind, params_hash, finished_at, path, data_asof FROM artifacts WHERE kind = ?"
                              " ORDER BY finished_at DESC, id DESC", (k,)).fetchall():
            path = Path(r["path"])
            if not inside(root, path / "manifest.json"):
                continue
            try:
                m = json.loads((path / "manifest.json").read_text())
            except FileNotFoundError:
                continue
            tables = [t for t in m.get("tables") or [] if isinstance(t, str) and TABLE_NAME.match(t)
                      and (path / f"{t}.parquet").is_file()]
            worker = m.get("host") != B.HOST
            want = (need_any or (worker and need_worker) or any(t not in t_any for t in tables)
                    or (worker and any(t not in t_worker for t in tables)))
            if want and r["id"] not in seen:
                seen.add(r["id"])
                out.append({"kind": k, "id": r["id"], "params_hash": r["params_hash"], "finished_at": r["finished_at"],
                            "data_asof": r["data_asof"], "files": ["manifest.json", *[f"{t}.parquet" for t in tables]]})
            need_any = False
            t_any |= set(tables)
            if worker:
                need_worker = False
                t_worker |= set(tables)
    return out


def previous_of(door: Door, kind: str) -> dict[str, Any]:
    """``previous_for`` without a hold (the dry run's mirror); 403 for a kind not burstable."""
    spec = _burstable(kind)
    if not door.db.exists():
        return {"previous": []}
    with closing(connect(door.db)) as conn:
        return {"previous": previous_for(conn, door.root, spec.name)}


def artifact_file(door: Door, kind: str, art_id: str, name: str) -> Path:
    """One file of an earlier artifact, for the mirror: 404 unless the kind is one a burstable kind reads, the id an
    artifact row under it, and the name its manifest.json or a table its manifest lists, under the root."""
    readable = {k for ks in B.LOOKUP_KINDS.values() for k in ks}
    if kind not in readable or not _ULID.match(art_id) or not door.db.exists():
        raise Refused(404, "not_found", "no such file")
    with closing(connect(door.db)) as conn:
        row = conn.execute("SELECT path FROM artifacts WHERE id = ? AND kind = ?", (art_id, kind)).fetchone()
    if row is None:
        raise Refused(404, "not_found", "no such file")
    path = Path(row["path"])
    if not inside(door.root, path / "manifest.json"):
        raise Refused(404, "not_found", "no such file")
    try:
        m = json.loads((path / "manifest.json").read_text())
    except FileNotFoundError as e:
        raise Refused(404, "not_found", "no such file") from e
    allowed = {"manifest.json"} | {f"{t}.parquet" for t in m.get("tables") or []
                                   if isinstance(t, str) and TABLE_NAME.match(t)}
    if name not in allowed:
        raise Refused(404, "not_found", "no such file")
    f = path / name
    if not inside(door.root, f) or not f.is_file():
        raise Refused(404, "not_found", "no such file")
    return f


# --- upload ---------------------------------------------------------------------------------------------------------

def _iso(v: Any, what: str) -> datetime:
    if not isinstance(v, str):
        raise Refused(422, "bad_manifest", f"{what} must be an ISO timestamp")
    try:
        t = parse_iso(v)
    except ValueError as e:
        raise Refused(422, "bad_manifest", f"{what} must be an ISO timestamp") from e
    if t.tzinfo is None:
        raise Refused(422, "bad_manifest", f"{what} must carry a zone")
    return t


def _check_types(m: dict[str, Any], kind: str) -> list[str]:
    """The uploaded fields ``build_manifest`` takes, by type -- before they reach it. Returns the tables."""
    def bad(why: str) -> Refused:
        return Refused(422, "bad_manifest", why)

    if m.get("host") != B.HOST:
        raise bad('a burst artifact says host: "burst"')
    b = m.get("burst")
    if not isinstance(b, dict) or set(b) != {"export", "ran_at", "finished_at", "machine"}:
        raise bad("burst is {export, ran_at, finished_at, machine}")
    e = b["export"]
    if not isinstance(e, dict) or set(e) != {"day", "digest", "exported_at"} or not isinstance(e["day"], str) \
            or not _DAY.match(e["day"]) or not isinstance(e["digest"], str) or not _HEX64.match(e["digest"]):
        raise bad("burst.export is {day: YYYY-MM-DD, digest: 64 hex, exported_at}")
    _iso(e["exported_at"], "burst.export.exported_at")
    _iso(b["ran_at"], "burst.ran_at")
    _iso(b["finished_at"], "burst.finished_at")
    mach = b["machine"]
    if not isinstance(mach, dict) or set(mach) != {"platform", "cores"} or not isinstance(mach["platform"], str) \
            or len(mach["platform"]) > 200 or not isinstance(mach["cores"], int) or isinstance(mach["cores"], bool):
        raise bad("burst.machine is {platform, cores}")
    tables = m.get("tables")
    if not isinstance(tables, list) or not all(isinstance(t, str) and TABLE_NAME.match(t) for t in tables) \
            or len(set(tables)) != len(tables):
        raise bad("tables is a list of distinct table names")
    for k in ("started_at", "finished_at"):
        _iso(m.get(k), k)
    for k, types in (("notes", list), ("provenance", list), ("code_sha", str), ("engine", str)):
        if not isinstance(m.get(k), types):
            raise bad(f"{k} must be a {types.__name__}")
    if not all(isinstance(n, str) for n in m["notes"]) or not all(isinstance(p, dict) for p in m["provenance"]):
        raise bad("notes are strings and provenance entries objects")
    for k in ("data_asof", "survivorship", "verdict", "verdict_detail", "lock_hash", "data_hash", "data_hash_note"):
        if m.get(k) is not None and not isinstance(m.get(k), str):
            raise bad(f"{k} must be a string or null")
    for k in ("threads", "peak_rss_bytes"):
        if m.get(k) is not None and (not isinstance(m.get(k), int) or isinstance(m.get(k), bool)):
            raise bad(f"{k} must be an integer")
    if not isinstance(m.get("cpu_seconds"), (int, float)) or isinstance(m.get("cpu_seconds"), bool) \
            or m["cpu_seconds"] < 0:
        raise bad("cpu_seconds must be a non-negative number")
    seed = m.get("seed")
    if seed is not None and (not isinstance(seed, dict) or not all(isinstance(k, str) and isinstance(v, int)
                                                                    for k, v in seed.items())):
        raise bad("seed is null or {name: integer}")
    for k in ("kernels", "runtime"):
        if m.get(k) is not None and not isinstance(m.get(k), dict):
            raise bad(f"{k} must be an object")
    return tables


def _reads(raw: bytes) -> list[dict[str, Any]]:
    from .provenance import _regenerable

    try:
        reads = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise Refused(422, "bad_file", "reads.json is not JSON") from e
    if not isinstance(reads, list) or not _depth_ok(reads, 3):
        raise Refused(422, "bad_file", "reads.json is a list of flat objects")
    out = []
    for r in reads:
        if not isinstance(r, dict) or not _READ_KEYS <= set(r) <= _READ_KEYS | {"artifact"} \
                or not isinstance(r["name"], str) or not isinstance(r["source"], str) \
                or not isinstance(r["sha256"], str) or not _HEX64.match(r["sha256"]) \
                or (r.get("artifact") is not None and not isinstance(r["artifact"], str)):
            raise Refused(422, "bad_file", "each read is {name, sha256, source, regenerable[, artifact]}")
        n = {"name": r["name"], "sha256": r["sha256"], "source": r["source"], "regenerable": _regenerable(r["source"])}
        if "artifact" in r:
            n["artifact"] = r["artifact"]
        out.append(n)
    return out


@dataclass
class DecodeBudget:
    """What one upload's tables may decode to, summed across them (module constants, M2)."""
    encoded: int = 0
    decoded: int = 0
    cells: int = 0


def _parquet(raw: bytes, name: str, *, budget: DecodeBudget) -> Any:
    """Readable Parquet, parsed from memory by pyarrow (no path, no pickle, no code), with distinct column names,
    inside the decode budget -> its Arrow schema. The metadata is read first and checked (rows, columns, cells,
    uncompressed bytes); then the file is decoded one batch at a time, string columns as dictionaries (so a
    repeated value is not expanded), and refused as soon as the decoded bytes pass the budget."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if len(raw) < 12 or raw[:4] != b"PAR1" or raw[-4:] != b"PAR1":
        raise Refused(422, "bad_file", f"{name} is not a Parquet file")

    def over(why: str) -> Refused:
        return Refused(422, "bad_file", f"{name} would decode past the door's budget ({why})")

    try:
        schema = pq.read_schema(pa.BufferReader(raw))
        md = pq.read_metadata(pa.BufferReader(raw))
        if md.num_columns > MAX_COLUMNS or md.num_rows > MAX_ROWS:
            raise over(f"{md.num_rows} rows x {md.num_columns} columns; at most {MAX_ROWS} x {MAX_COLUMNS}")
        budget.cells += md.num_rows * md.num_columns
        budget.encoded += sum(md.row_group(i).total_byte_size for i in range(md.num_row_groups))
        if budget.cells > MAX_CELLS or budget.encoded > MAX_ENCODED_BYTES:
            raise over(f"{budget.cells} cells, {budget.encoded} uncompressed bytes so far; at most {MAX_CELLS} "
                       f"and {MAX_ENCODED_BYTES}")
        strings = [f.name for f in schema if pa.types.is_string(f.type) or pa.types.is_large_string(f.type)
                   or pa.types.is_binary(f.type) or pa.types.is_large_binary(f.type)]
        pf = pq.ParquetFile(pa.BufferReader(raw), read_dictionary=strings)
        # round 2 (N3): a dictionary column's batches carry their row group's dictionary -- the same values again,
        # growing as pages are read -- so each batch counts its indices, and the dictionary counts only by how much
        # it grew past the largest seen for that column in that row group: the distinct values, once. (A dictionary
        # is never larger than the column chunk's uncompressed pages, which MAX_ENCODED_BYTES bounds, so one batch
        # in memory stays within that.)
        for rg in range(pf.num_row_groups):
            largest: dict[int, int] = {}
            for batch in pf.iter_batches(batch_size=BATCH_ROWS, row_groups=[rg]):
                for i, col in enumerate(batch.columns):
                    if pa.types.is_dictionary(col.type):
                        size = col.dictionary.nbytes
                        budget.decoded += col.indices.nbytes + max(0, size - largest.get(i, 0))
                        largest[i] = max(size, largest.get(i, 0))
                    else:
                        budget.decoded += col.nbytes
                del batch
                if budget.decoded > MAX_DECODED_BYTES:
                    raise over(f"over {MAX_DECODED_BYTES} decoded bytes")
    except Refused:
        raise
    except Exception as e:  # noqa: BLE001 - any parse failure is a refusal, named
        raise Refused(422, "bad_file", f"{name} is not a readable Parquet file ({type(e).__name__})") from e
    if len(set(schema.names)) != len(schema.names):
        raise Refused(422, "bad_file", f"{name} has duplicate column names")
    return schema


def _files(obj: Any, kind: str, tables: list[str]) -> tuple[dict[str, bytes], dict[str, Any], list[dict[str, Any]]]:
    """``{table: Parquet bytes}`` keyed by names from the kind's allow-list, their Arrow schemas, and the parsed
    reads. Each file's base64 text is dropped from ``obj`` as soon as it is decoded (the memory budget)."""
    allowed = B.BURST_TABLES[kind]
    if not isinstance(obj, dict):
        raise Refused(422, "bad_file", "files is an object of name: base64")
    extra = [t for t in tables if t not in allowed]
    if extra:
        raise Refused(422, "bad_file", f"{kind} never produces {', '.join(extra)} (allowed: {', '.join(sorted(allowed))})")
    want = {f"{t}.parquet" for t in tables} | {B.READS_FILE}
    if set(obj) != want:
        odd = sorted(set(obj) ^ want)
        raise Refused(422, "bad_file", f"the files must be exactly the listed tables' Parquet and {B.READS_FILE}; "
                                       f"these are not: {', '.join(n[:80] for n in odd)}")

    def decode(name: str) -> bytes:
        v = obj[name]
        obj[name] = None
        if not isinstance(v, str):
            raise Refused(422, "bad_file", f"{name} is not base64 text")
        try:
            return base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError) as e:
            raise Refused(422, "bad_file", f"{name} is not base64") from e

    out: dict[str, bytes] = {}
    schemas: dict[str, Any] = {}
    budget = DecodeBudget()
    for t in sorted(allowed):                     # the name is the allow-list's string, never the upload's
        if t in tables:
            raw = decode(f"{t}.parquet")
            schemas[t] = _parquet(raw, f"{t}.parquet", budget=budget)
            out[t] = raw
    return out, schemas, _reads(decode(B.READS_FILE))


def _compatible(a: Any, b: Any) -> bool:
    """Two Arrow types the same column may carry from run to run: equal, one of them all-null, or both numeric (an
    integer column becomes float when a run has a missing value; round 2)."""
    import pyarrow as pa

    def numeric(t: Any) -> bool:
        return pa.types.is_integer(t) or pa.types.is_floating(t)

    return a == b or pa.types.is_null(a) or pa.types.is_null(b) or (numeric(a) and numeric(b))


def check_schemas(kind: str, schemas: dict[str, Any], *, db: Path, root: Path, code_sha: str) -> None:
    """Fix round 1, M3: each uploaded table has the column names (in order) and types of the same table in the
    droplet's newest worker artifact of the kind -- compared only when that artifact was made by this deployment's
    code (``code_sha``, round 2): a burst runs the deployed code (code_drift), and a deploy may change a table's
    columns, so an artifact older code made says nothing about what this code writes. 422 schema_mismatch
    otherwise. All-null columns match any type; integer and float match each other."""
    import pyarrow.parquet as pq

    from ..products.io import _newest

    if not schemas or not Path(db).exists():
        return
    got = _newest(kind, None, db, None, True)
    if got is None or not inside(root, got[1] / "manifest.json") or got[0].get("code_sha") != code_sha:
        return
    for t, up in schemas.items():
        f = got[1] / f"{t}.parquet"
        if not inside(root, f) or not f.is_file():
            continue
        want = pq.read_schema(f)
        if up.names != want.names or not all(_compatible(up.field(n).type, want.field(n).type) for n in up.names):
            show = lambda sc: ", ".join(f"{fl.name}:{fl.type}" for fl in sc)[:300]  # noqa: E731
            raise Refused(422, "schema_mismatch", f"{t}: the upload's columns ({show(up)}) are not those of the "
                                                  f"droplet's newest worker {kind} {got[0].get('id')} ({show(want)})")


#: EXP-Q01's once-only holdout tables (products/q01.py HOLDOUT_TABLES; restated so the door never imports pandas
#: models). A burst never evaluates them (fix round 1, I2): a Q01 upload may carry them only unchanged from the
#: droplet's newest artifact holding a holdout.
Q01_HOLDOUT_TABLES = ("holdout", "holdout_returns", "holdout_costs", "holdout_regimes", "holdout_ic",
                      "holdout_deciles", "gates")


def check_holdout(kind: str, blobs: dict[str, bytes], *, db: Path, root: Path) -> None:
    """A burst never evaluates EXP-Q01's once-only holdout (fix round 1, I2). An upload holding any holdout table
    (round 2) must carry each one equal (canonical frame hash) to the droplet's newest artifact holding a holdout --
    i.e. carried forward, never new. 409 holdout_worker_only otherwise."""
    import io

    import pandas as pd

    from ..products.io import _newest
    from .provenance import frame_hash

    present = [t for t in Q01_HOLDOUT_TABLES if t in blobs]
    if kind != "models.xs_lgbm" or not present:
        return
    # round 2 (N2): any holdout table is checked, not only `holdout`; an INSUFFICIENT DATA run publishes an empty
    # `gates` and nothing else, which is no holdout
    if present == ["gates"] and pd.read_parquet(io.BytesIO(blobs["gates"])).empty:
        return
    refuse = Refused(409, "holdout_worker_only", "this upload carries an EXP-Q01 holdout the droplet did not evaluate: "
                                                 "the once-only holdout evaluation runs on the worker only")
    got = _newest(kind, None, db, "holdout", False) if Path(db).exists() else None
    if got is None or not inside(root, got[1] / "manifest.json"):
        raise refuse
    for t in Q01_HOLDOUT_TABLES:
        if t not in blobs:
            continue
        f = got[1] / f"{t}.parquet"
        if not inside(root, f) or not f.is_file():
            raise refuse
        if frame_hash(pd.read_parquet(io.BytesIO(blobs[t]))) != frame_hash(pd.read_parquet(f)):
            raise refuse


_DIGESTS: dict[tuple[str, int, int], str] = {}


def _export(door: Door, day: str) -> tuple[str, dict[str, Any]]:
    """(digest, record) of the export of ``day`` the droplet holds, the digest computed here (cached per file
    state); 409 when it holds none."""
    from ..warehouse.export import ExportFailed, export_digest, read_record

    d = Path(door.exports) / day                   # day matched YYYY-MM-DD above
    if not d.is_dir() or d.is_symlink():
        raise Refused(409, "no_such_export", f"the droplet holds no export of {day}: the artifact was not made from "
                                             "one it has (pruned, or never exported)")
    try:
        record = read_record(d)
        st = (d / "export.json").stat()
        key = (str(d), st.st_mtime_ns, sum(p.stat().st_size for p in d.iterdir() if p.is_file()))
        if key not in _DIGESTS:
            _DIGESTS[key] = export_digest(d)
    except (ExportFailed, OSError) as e:
        raise Refused(409, "no_such_export", f"the droplet's export of {day} is not a verified export ({e})") from e
    return _DIGESTS[key], record


def verify_artifact_reads(reads: list[dict[str, Any]], *, db: Path, root: Path, exported_at: datetime) -> None:
    """Every earlier-artifact read, recomputed from the droplet's own store: a lookup must find what it found (the
    droplet's newest, now), a table read must hash as the droplet's table does, and every artifact read must have
    been published before the export the run's data came from. 409 stale_state otherwise."""
    import pandas as pd

    from ..products.io import _newest
    from .kinds import REGISTRY
    from .provenance import frame_hash

    def stale(why: str) -> Refused:
        return Refused(409, "stale_state", f"{why} -- burst again from the newest export")

    def before_export(finished: str, what: str) -> None:
        if parse_iso(finished) > exported_at:
            raise stale(f"{what} was published after the export the run read ({exported_at.isoformat()}): its state "
                        "is newer than the data")

    for r in reads:
        name, src = str(r.get("name")), str(r.get("source"))
        is_artifact = any(p.strip().startswith("artifact") for p in src.split(","))
        if name.startswith("artifact-lookup: "):
            m = _LOOKUP.match(name)
            if m is None or m["kind"] not in REGISTRY:
                raise Refused(409, "not_from_export", f"an artifact lookup the door cannot check: {name[:160]}")
            ph = None if m["ph"] == "None" else m["ph"]
            t = None if m["t"] == "None" else m["t"]
            got = _newest(m["kind"], ph, db, t, bool(m["wo"])) if Path(db).exists() else None
            if got is not None and not inside(root, got[1] / "manifest.json"):
                raise Refused(409, "stale_state", f"the droplet's {m['kind']} artifact lies outside its root")
            found = None if got is None else got[0].get("id")
            if r.get("sha256") != frame_hash({"artifact": found}):
                raise stale(f"the run looked {m['kind']} up and found {r.get('artifact') or 'none'}; the droplet's "
                            f"answer is {found or 'none'}")
            if got is not None:
                before_export(str(got[0].get("finished_at")), f"{m['kind']} {found}")
        elif name.startswith("artifact: "):
            m = _TABLE_READ.match(name)
            if m is None or m["kind"] not in REGISTRY:
                raise Refused(409, "not_from_export", f"an artifact read the door cannot check: {name[:160]}")
            row = None
            if Path(db).exists():
                with closing(connect(db)) as conn:
                    row = conn.execute("SELECT path, finished_at FROM artifacts WHERE id = ? AND kind = ?",
                                       (m["id"], m["kind"])).fetchone()
            if row is None:
                raise stale(f"the run read {m['kind']} {m['id']}, which the droplet does not hold")
            f = Path(row["path"]) / f"{m['t']}.parquet"
            if not inside(root, f):
                raise Refused(409, "stale_state", f"the droplet's {m['kind']} {m['id']} lies outside its root")
            if r.get("sha256") != frame_hash(pd.read_parquet(f) if f.exists() else None):
                raise stale(f"the run's {m['kind']} {m['id']}/{m['t']} is not the droplet's")
            before_export(row["finished_at"], f"{m['kind']} {m['id']}")
        elif is_artifact:
            raise Refused(409, "not_from_export", f"an artifact read the door cannot check: {name[:160]}")


def _published(conn: Any, job: Any) -> dict[str, Any] | None:
    row = conn.execute("SELECT path FROM artifacts WHERE id = ?", (job.artifact_id,)).fetchone() \
        if job.artifact_id else None
    if row is None:
        return None
    try:
        m = json.loads((Path(row["path"]) / "manifest.json").read_text())
    except (FileNotFoundError, ValueError):
        return None
    return {"id": job.artifact_id, "host": m.get("host", "worker"), "data_hash": m.get("data_hash")}


def accept_upload(door: Door, job_id: str, body: bytes | list[bytes], *,
                  now: datetime | None = None) -> dict[str, Any]:
    """Verify an upload (module docstring, in that order) and publish it. -> the published artifact. ``body`` may be
    a one-element list, emptied here once parsed, so the raw body is not held beside the decoded files (M2)."""
    from .artifacts import build_manifest, prune
    from .provenance import data_hash as compute_data_hash
    from .queue import canonical_params, get_job

    now = now or datetime.now(UTC)
    raw_body = body.pop() if isinstance(body, list) else body
    obj = _json(raw_body)
    del raw_body
    if set(obj) != {"manifest", "files"} or not isinstance(obj["manifest"], dict):
        raise Refused(422, "bad_request", 'the body is {"manifest": {...}, "files": {...}}')
    m: dict[str, Any] = obj["manifest"]
    if len(json.dumps(m)) > MAX_MANIFEST_BYTES:
        raise Refused(422, "bad_manifest", f"the manifest is over {MAX_MANIFEST_BYTES} bytes")
    if not _ULID.match(job_id) or not door.db.exists():
        raise Refused(404, "not_found", f"no job {job_id[:40]}")
    with closing(connect(door.db)) as conn:
        job = get_job(conn, job_id)
        if job is None:
            raise Refused(404, "not_found", f"no job {job_id}")
        _burstable(job.kind)
        _burstable(m.get("kind"))
        if job.submitted_by != "burst":
            raise Refused(409, "scheduled", f"{job.id} was queued by {job.submitted_by}: the worker runs it and publishes "
                                            "its artifact; a burst uploads only for a job the burst door holds")
        if job.state == "running":
            raise Refused(409, "claimed", f"the worker claimed {job.id}; its own artifact will be published")
        if job.state == "done":
            raise Refused(409, "already_done", f"{job.id} is done", published=_published(conn, job))
        if job.state != "queued":
            raise Refused(409, "not_queued", f"{job.id} is {job.state}")
        try:
            same = (m.get("kind") == job.kind and m.get("params_hash") == job.params_hash
                    and isinstance(m.get("params"), dict) and canonical_params(m["params"]) == canonical_params(job.params))
        except ValueError:
            same = False
        if not same:
            raise Refused(409, "job_mismatch", f"the artifact was made for other params or another kind than the queued "
                                               f"job {job.id} ({job.kind}); burst again with the job's params exactly")
    tables = _check_types(m, job.kind)
    blobs, schemas, reads = _files(obj["files"], job.kind, tables)
    obj["files"] = None
    # the manifest the worker's publish would write for this job, from the job row, the reads and the uploaded
    # fields: build_manifest's checks (engine, verdict, seed) and its data_hash, computed here
    shape = SimpleNamespace(id=job.id, kind=job.kind, params=job.params, params_hash=job.params_hash,
                            started_at=m["started_at"])
    result = {k: m.get(k) for k in ("data_asof", "provenance", "notes", "survivorship", "engine", "tables", "verdict",
                                    "verdict_detail", "seed", "lock_hash", "threads", "kernels", "runtime")}
    result.update(reads=reads, unseeded=[], unrecorded=[])
    try:
        rebuilt = build_manifest(shape, result, code_sha=m["code_sha"], finished_at=m["finished_at"],  # type: ignore[arg-type]
                                 cpu_seconds=m["cpu_seconds"], peak_rss_bytes=m.get("peak_rss_bytes"))
    except ValueError as e:
        raise Refused(422, "bad_manifest", str(e)) from e
    keys_up, keys_want = set(m) - {"host", "burst"}, set(rebuilt) - {"reads"}
    if keys_up != keys_want:
        raise Refused(422, "bad_manifest", "fields publish does not write, or missing: "
                                           + ", ".join(sorted(keys_up ^ keys_want))[:300])
    differ = sorted(k for k in keys_want if rebuilt[k] != m[k])
    if differ:
        raise Refused(422, "inconsistent_manifest", "the manifest differs from the one the door rebuilds from the job "
                                                    "and the uploaded reads in: " + ", ".join(differ))
    if m.get("data_hash") is None:
        raise Refused(422, "unrecorded_reads", "a burst artifact must record every read (its data_hash is null)")
    if m["data_hash"] != compute_data_hash(reads):     # implied by the rebuild above; stated, not asserted (M7)
        raise Refused(422, "inconsistent_manifest", "the data_hash is not the hash of the uploaded reads")
    live = [r["name"][:120] for r in reads if not r["regenerable"]]
    if live:
        raise Refused(409, "not_from_export", "the run read data no export holds: " + "; ".join(live[:5]))
    fixtures = B.fixture_reads(reads)
    if fixtures:
        raise Refused(409, "not_from_export", "the run was served the committed fixtures, not the export (its warehouse "
                                              "was not fresh at the export's moment): " + "; ".join(fixtures[:5]))
    if not door.code_sha or door.code_sha == "unknown" or m["code_sha"] != door.code_sha \
            or m.get("lock_hash") != door.lock_hash:
        raise Refused(409, "code_drift", f"the artifact was made by code {m['code_sha']} (lock {str(m.get('lock_hash'))[:12]}); "
                                         f"this host runs {door.code_sha} (lock {str(door.lock_hash)[:12]})")
    e = m["burst"]["export"]
    digest, record = _export(door, e["day"])
    if digest != e["digest"] or str(record.get("exported_at")) != e["exported_at"]:
        raise Refused(409, "no_such_export", f"the droplet's export of {e['day']} is not the one the artifact was made "
                                             "from (its digest or exported_at differs)")
    exported_at = parse_iso(str(record["exported_at"]))
    if parse_iso(m["started_at"]) != exported_at:
        raise Refused(422, "clock", "started_at must be the export's exported_at: the run's clock is its data's")
    if parse_iso(m["finished_at"]).timestamp() > now.timestamp() + B.TS_WINDOW_S:
        raise Refused(422, "clock", "finished_at is in the future")
    verify_artifact_reads(reads, db=door.db, root=door.root, exported_at=exported_at)
    check_holdout(job.kind, blobs, db=door.db, root=door.root)
    check_schemas(job.kind, schemas, db=door.db, root=door.root, code_sha=door.code_sha)

    # a staging directory of its own (M4): two uploads for one job never share one
    (Path(door.root) / ".staging").mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"burst-{job.id}-", dir=Path(door.root) / ".staging"))
    try:
        for t, raw in blobs.items():
            (staging / f"{t}.parquet").write_bytes(raw)
        full = {**{k: m[k] for k in m if k not in ("host", "burst")}, "reads": reads,
                "host": B.HOST, "burst": m["burst"]}
        with closing(connect(door.db)) as conn:
            ok, state = publish_burst(conn, job.id, kind=job.kind, params_hash_=job.params_hash, staging=staging,
                                      root=Path(door.root), manifest=full, cpu_seconds=float(m["cpu_seconds"]),
                                      peak_rss_bytes=m.get("peak_rss_bytes"), now=now)
            if not ok:
                if state == "scheduled":
                    raise Refused(409, "scheduled", f"{job.id} is not a burst job")
                if state == "running":
                    raise Refused(409, "claimed", f"the worker claimed {job.id} meanwhile")
                if state == "done":
                    raise Refused(409, "already_done", f"{job.id} is done", published=_published(conn, get_job(conn, job.id)))
                raise Refused(409, "not_queued", f"{job.id} is {state}")
            prune(conn, kind=job.kind)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"id": job.id, "kind": job.kind, "path": f"{job.kind}/{job.id}", "data_hash": m["data_hash"],
            "host": B.HOST}
