"""What a number was made from (Task 3.3): every read a job makes is hashed, its seed is recorded, and the
manifest names both -- with the lock file and the kernels it ran on -- so ``tools/regenerate.py`` can make the
number again from a warehouse export and say whether it came out the same.

THE CANONICAL HASH (``frame_hash``)

The brief asked for the SHA-256 of a frame's "canonical Parquet bytes". Parquet bytes are not canonical: the
same values give different files under another pyarrow (writer version, statistics, page layout), another
compression, a microsecond index where the last run had nanoseconds (Task 3.1's review found exactly that), or
pandas' metadata blob. So this hashes a canonical encoding of the VALUES instead, defined here and nowhere else:

1. A Series is the one-column frame ``series.to_frame()``. Anything that is not a table (a dict, a list of
   dicts, a scalar) is hashed as canonical JSON: sorted keys, no whitespace, NaN as null, -0.0 as 0.0.
2. The index is dropped when it is the default ``RangeIndex(0..n-1)``; any other index is data, and each level
   becomes a column named by its name (``index`` / ``level_<i>`` when unnamed, as ``reset_index`` names it).
3. Column names are strings; the columns are taken in name order, so column order is not part of the hash.
   Two columns with one name are refused.
4. Each column has one kind, decided by its non-missing values, not its dtype:
     ``n`` every value missing (an all-NaN float column and an all-None object column are one column);
     ``f`` numbers -- int, uint, float, Decimal -- as IEEE float64 little-endian, -0.0 as 0.0 (an integer
       beyond 2**53 loses its low bits: stated, and no read here holds one);
     ``b`` booleans (True is not 1.0);
     ``t`` naive datetimes, dates and ``datetime.date`` objects, as a pair of int64s -- whole seconds since
       the epoch, nanoseconds within the second -- so the unit (s, ms, us, ns) does not matter, a DATE equals
       its midnight, and 9999-12-31 does not overflow;
     ``z`` tz-aware datetimes, converted to UTC, the same pair (one instant in any zone; not the naive time
       with the same digits);
     ``d`` timedeltas, the same pair;
     ``s`` text: a str as itself, any other value as canonical JSON (``default=str``).
   Missing -- NaN, None, NaT, ``pd.NA`` -- is one value in every kind: a null flag, the value zeroed.
5. Rows are sorted by their encoded values (the columns in name order, null flag before value): row order is
   not part of the hash. A product whose output depends on the order of a read is caught by the table
   comparison, not here.
6. The bytes hashed: ``json([[name, kind], ...])``, the row count, then per column, in name order, its null
   flags (uint8) and its values in the sorted row order -- ``<f8``, ``u1``, the time pairs as interleaved
   ``<i8``, text as one JSON array of strings (UTF-8; the quoting keeps ("ab", "c") apart from ("a", "bc")).
   Every width and byte order is explicit, so macOS and Linux agree, and nothing pyarrow or pandas writes is
   hashed, so a patch bump of either changes nothing.

``data_hash`` is the SHA-256 of the sorted ``name<TAB>sha256`` lines, one a read: the brief's "sorted per-read
hashes" with each read's name bound to its hash (fix round 1), so two reads cannot swap their data unnoticed.

THE RECORDER

``Recorder`` is the one place a job's reads, seeds and draws are written down. ``JobContext`` owns one and
hands it out three ways: ``ctx.read(name, frame)`` for a read a product makes itself, a ``RecordingConnection``
around ``ctx.warehouse`` (every ``execute`` whose rows are fetched), a ``RecordingMarket`` around
``ctx.market`` (every Dataset the facade returns, its source from the Dataset's provenance), and -- while it is
active -- ``products.io``'s reads of earlier artifacts. Seeds come from ``ctx.seed(value, name)``.

While active, it also watches for random numbers drawn without a seed: a generator from
``numpy.random.default_rng()`` with no argument that was drawn from (its state moved; arch builds one it never
uses, and a module import -- scipy.stats draws doc examples -- is not the job's), a draw from numpy's legacy
global state, a draw from the ``random`` module. Not seen at runtime, so forbidden in the source instead
(tests/test_jobs_provenance.py reads the syntax tree): ``from numpy.random import default_rng`` at module level
(the name is bound before the recorder patches it) and an unseeded ``np.random.RandomState()`` (its own state,
not the global one). Each is written to
``unseeded`` and fails the manifest. What it cannot see: a ``Generator(PCG64())`` built without a seed, an
unseeded RNG inside a C/Rust extension or a subprocess (the Rust kernels and Kronos take their seed as an
argument, so they cannot run unseeded), and thread-count-dependent numerics -- which is why the manifest
records ``threads`` and the kernels' engine, and ``regenerate.py`` re-runs with the same ones.
"""

from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import math
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

#: Sources a re-run can serve again from a warehouse export and this checkout (``regenerate.py``): the
#: warehouse itself, the committed fixtures (they are in the image and the repository, so the code SHA covers
#: them), earlier artifacts (immutable; pinned by id), and a facade note that serves no data of its own.
REGENERABLE_PREFIXES = ("warehouse", "fixture", "artifact", "derived", "none")


class UnseededDraw(ValueError):
    """A product drew random numbers with no seed, or declared it draws them and recorded none."""


_INGEST = ("ingest.universes", "ingest.bars_daily", "ingest.bars_minute", "ingest.fred_warehouse", "ingest.factors",
           "ingest.sec_facts", "ingest.holdings_13f", "ingest.option_snapshots", "ingest.corp_actions")
#: The kinds that write /data/warehouse.duckdb (ingest.fred writes an artifact only): the nightly export waits for
#: a running one, and no schedule of one may fall inside the export's window (tests/test_jobs_schedules.py).
WAREHOUSE_WRITERS = frozenset(_INGEST)

#: Kinds whose reads do not all pass through the recorder. Their manifests publish ``data_hash: null`` with this
#: reason, so a hash never claims more than was hashed. (tests/test_jobs_provenance.py holds every direct read in
#: products/, signals/ and jobs/handlers/ to a declaration here or a recorded route.)
UNRECORDED_READS: dict[str, str] = {
    **{k: "the vendors' answers are fetched over HTTP by the ingest itself (data/http.py), not through the "
          "recorder; it writes the warehouse" for k in _INGEST},
    "signals.daily": "the live engine's /api/research and the last signal document on the signals volume are read "
                     "outside the recorder (the document itself carries the hash of the bars it used)",
    "signals.overlay": "the signal documents on the signals volume are read outside the recorder",
    "desk.report": "the live engine's routes are read over HTTP outside the recorder",
}

#: Kinds tools/regenerate.py refuses, and why: a re-run could not be faithful, or would act on the world.
NOT_REGENERABLE: dict[str, str] = {
    **{k: "an ingest writes the warehouse from the vendors' answers; it is what an export is made of, not "
          "something re-run from one" for k in _INGEST},
    "ingest.fred": "an ingest fetches a vendor's answer; no export holds it",
    "signals.daily": "it writes the desk's signal documents and the month's parameter freeze, and reads the live "
                     "engine",
    "signals.overlay": "it writes the desk's overlay documents",
    "signals.freeze": "it is published by signals.daily, never run as a job",
    "desk.report": "it reads the live engine's session as it was at 16:30",
    "kronos.shadow": "every row carries the wall-clock time it was made (made_at), and Kronos runs in its own "
                     "environment, whose lock this manifest does not record",
    "ops.selftest": "it reports the process it ran in (pid, platform, interpreter)",
    "farm.sweep": "which cells it evaluates depends on a wall-clock budget (budget_minutes, time.monotonic) and on "
                  "the previous artifact's ran_at order, so a re-run on another machine or day evaluates others",
}

#: Columns that stamp the wall-clock time a run happened; regenerate.py compares every other column and says
#: which it masked.
RUN_STAMPS: dict[str, frozenset[str]] = {
    "farm.sweep": frozenset({"ran_at"}),
    "models.xs_lgbm": frozenset({"selected_at", "evaluated_at"}),
    "regime.hmm": frozenset({"evaluated_at"}),
}


def check_seed(kind: str, result: dict[str, Any], *, draws_random: bool | None = None) -> None:
    """Refuse a result that drew random numbers without a seed, or whose kind declares draws and recorded none."""
    if draws_random is None:
        from .kinds import REGISTRY

        spec = REGISTRY.get(kind)
        draws_random = bool(spec and spec.draws_random)
    unseeded = list(result.get("unseeded") or [])
    if unseeded:
        raise UnseededDraw(f"{kind} drew random numbers without a seed: {'; '.join(unseeded)}")
    if draws_random and not result.get("seed"):
        raise UnseededDraw(f"{kind} declares it draws random numbers (KindSpec.draws_random) but recorded no seed "
                           "(ctx.seed / ctx.rng): its numbers could not be made again")


def unrecorded_note(kind: str, result: dict[str, Any]) -> str | None:
    """Why this result's data_hash must be null, or None when every read was recorded."""
    if "reads" not in result:
        return "no reads were recorded for this result (it was not built by a recording job context)"
    why = []
    if kind in UNRECORDED_READS:
        why.append(UNRECORDED_READS[kind])
    why += list(result.get("unrecorded") or [])
    return "; ".join(why) or None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def data_hash(reads: Iterable[dict[str, Any]]) -> str:
    """The SHA-256 of the sorted ``name<TAB>sha256`` lines, one a read. A deliberate strengthening of the brief's
    "hash of the sorted per-read hashes" (fix round 1): with the names bound, two reads cannot trade their data and
    leave the hash unchanged. It changes the value for artifacts made from here on; nothing stored is rewritten."""
    return sha256_text("\n".join(sorted(f"{r['name']}\t{r['sha256']}" for r in reads)))


def legacy_data_hash(reads: Iterable[dict[str, Any]]) -> str:
    """The definition before fix round 1 (the sorted per-read hashes alone): regenerate.py recognises a manifest
    made with it, so its message can say so."""
    return sha256_text("\n".join(sorted(str(r["sha256"]) for r in reads)))


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def lock_path() -> Path:
    """The dependency lock this process was installed from: ``quant/uv.lock`` under the repository root
    (``/app/quant/uv.lock`` in the image -- the pydeps stage syncs ``--frozen`` from it and the runtime stage
    copies ``/app/quant`` whole, so the installed lock is the one on disk)."""
    from ..config import REPO_ROOT

    return REPO_ROOT / "quant" / "uv.lock"


def lock_hash() -> str | None:
    return file_sha256(lock_path())


def checkout_sha() -> str:
    """The code this process runs: the image's build SHA (``OHCAMEL_GIT_SHA``), else the checkout's HEAD."""
    import os
    import subprocess

    from ..config import REPO_ROOT

    env = os.environ.get("OHCAMEL_GIT_SHA")
    if env and env != "unknown":
        return env
    try:
        return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def runtime_info() -> dict[str, str]:
    """The interpreter and platform: recorded, and a difference only warns (regenerate.py)."""
    import platform

    return {"python": platform.python_version(), "platform": platform.platform()}


def kernels_info() -> dict[str, Any]:
    """The engine the kernels dispatcher chose for this process, and the wheel API it speaks when Rust."""
    from .. import kernels

    engine = getattr(kernels, "ENGINE", None) or "python"
    return {"engine": engine, "api": kernels.API_VERSION if engine == "rust" else None}


# --- the canonical hash --------------------------------------------------------------------------------------------

def _json_default(o: Any) -> Any:
    """What canonical JSON makes of a value json cannot encode. A frame or series nested in a dict is hashed whole
    (``frame_hash``) -- never through its repr, which pandas truncates to the head and tail -- and anything else
    without a defined encoding is refused rather than hashed by whatever its repr says (fix round 1)."""
    import numpy as np
    import pandas as pd

    if isinstance(o, (pd.DataFrame, pd.Series)):
        return {"__frame_sha256__": frame_hash(o)}
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (np.ndarray, np.generic)):
        return o.tolist()
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=repr)
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"{type(o).__name__} has no canonical encoding; it would be hashed by its repr, which need not "
                    "show all of it -- convert it to a frame, a list or a scalar before it is read")


def _name_default(o: Any) -> Any:
    """For a read's NAME only (a label, not data): anything prints."""
    try:
        return _json_default(o)
    except TypeError:
        return str(o)


def name_json(o: Any) -> str:
    return json.dumps(_norm_json(o), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=_name_default)


def _norm_json(o: Any) -> Any:
    if isinstance(o, float):
        if math.isnan(o):
            return None
        return 0.0 if o == 0 else o
    if isinstance(o, dict):
        return {str(k): _norm_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_norm_json(v) for v in o]
    if hasattr(o, "item") and not hasattr(o, "__len__"):   # a numpy scalar
        return _norm_json(o.item())
    return o


def canonical_json(o: Any) -> str:
    return json.dumps(_norm_json(o), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      default=_json_default, allow_nan=False)


_PER_SECOND = {"s": 1, "ms": 10**3, "us": 10**6, "ns": 10**9}
_EPOCH = datetime(1970, 1, 1)


def _pair(sec: Any, sub: Any) -> Any:
    """A time as (whole seconds since the epoch, nanoseconds within the second): no unit, no 2262 limit."""
    import numpy as np

    out = np.empty(len(sec), dtype=[("s", "<i8"), ("n", "<i8")])
    out["s"] = sec
    out["n"] = sub
    return out


def _split_units(arr: Any) -> tuple[Any, Any]:
    import numpy as np

    unit, _ = np.datetime_data(arr.dtype)
    base = "datetime64" if np.issubdtype(arr.dtype, np.datetime64) else "timedelta64"
    if unit not in _PER_SECOND:
        arr = arr.astype(f"{base}[s]")
        unit = "s"
    v = arr.view("int64")
    per = _PER_SECOND[unit]
    sec, rem = np.divmod(v, per)
    return sec, rem * (10**9 // per)


def _epoch_parts(v: Any) -> tuple[int, int]:
    import numpy as np
    import pandas as pd

    if isinstance(v, pd.Timestamp):
        if v.tzinfo is not None:
            v = v.tz_convert("UTC").tz_localize(None)
        v = v.to_datetime64()
    if isinstance(v, np.datetime64):
        sec, sub = _split_units(np.array([v]))
        return int(sec[0]), int(sub[0])
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone(UTC).replace(tzinfo=None)
        d = v - _EPOCH
        return d.days * 86400 + d.seconds, d.microseconds * 1000
    d = v - _EPOCH.date()                                   # a datetime.date: its midnight
    return d.days * 86400, 0


def _column(values: Any) -> tuple[str, Any, Any]:
    """(kind, null flags as a bool array, canonical values as a numpy array) for one column."""
    import numpy as np
    import pandas as pd

    s = pd.Series(values).reset_index(drop=True)
    null = s.isna().to_numpy(dtype=bool)
    n = len(s)
    if n == 0 or null.all():
        return "n", null, np.zeros(n, dtype="<f8")
    dt = s.dtype
    if isinstance(dt, pd.DatetimeTZDtype):
        sec, sub = _split_units(s.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy())
        return "z", null, _pair(np.where(null, 0, sec), np.where(null, 0, sub))
    if isinstance(dt, pd.CategoricalDtype):
        s, dt = s.astype(object), np.dtype(object)
    elif not isinstance(dt, np.dtype):                    # nullable and arrow-backed extension dtypes
        if pd.api.types.is_bool_dtype(dt):
            s, dt = s.astype(object), np.dtype(object)
        elif pd.api.types.is_numeric_dtype(dt):
            vals = s.to_numpy(dtype="float64", na_value=0.0)
            return "f", null, (np.where(null, 0.0, vals) + 0.0).astype("<f8")
        else:
            s, dt = s.astype(object), np.dtype(object)
    if dt == np.dtype(object):
        kind = {"string": "s", "integer": "f", "floating": "f", "mixed-integer-float": "f", "decimal": "f",
                "boolean": "b", "datetime": "t", "datetime64": "t", "date": "t"}.get(
            pd.api.types.infer_dtype(s, skipna=True), "x")
        if kind == "t":
            present = s[~null]
            aware = [getattr(v, "tzinfo", None) is not None for v in present]
            kind = "s" if any(aware) and not all(aware) else ("z" if all(aware) else "t")
            if kind != "s":
                parts = [(0, 0) if m else _epoch_parts(v) for v, m in zip(s.to_numpy(), null, strict=True)]
                return kind, null, _pair(np.array([p[0] for p in parts], dtype="<i8"),
                                         np.array([p[1] for p in parts], dtype="<i8"))
        if kind == "f":
            vals = np.array([0.0 if m else float(v) for v, m in zip(s.to_numpy(), null, strict=True)], dtype="<f8")
            return "f", null, (vals + 0.0).astype("<f8")
        if kind == "b":
            return "b", null, np.where(null, False, s.to_numpy()).astype("u1")
        if kind == "s":
            return "s", null, np.where(null, "", s.to_numpy()).astype(object)
        text = [("" if m else (v if isinstance(v, str) else canonical_json(v)))
                for v, m in zip(s.to_numpy(), null, strict=True)]
        return "s", null, np.array(text, dtype=object)
    if dt == np.dtype(bool):
        return "b", null, s.to_numpy().astype("u1")
    if np.issubdtype(dt, np.number):
        vals = s.to_numpy(dtype="float64", na_value=0.0)
        return "f", null, (np.where(null, 0.0, vals) + 0.0).astype("<f8")   # + 0.0 turns -0.0 into 0.0
    if np.issubdtype(dt, np.datetime64) or np.issubdtype(dt, np.timedelta64):
        sec, sub = _split_units(s.to_numpy())
        kind = "t" if np.issubdtype(dt, np.datetime64) else "d"
        return kind, null, _pair(np.where(null, 0, sec), np.where(null, 0, sub))
    return "s", null, np.array(["" if m else canonical_json(v) for v, m in zip(s.to_numpy(), null, strict=True)],
                               dtype=object)


def _as_frame(obj: Any) -> Any:
    import pandas as pd

    if isinstance(obj, pd.Series):
        return obj.to_frame()
    return obj


def _strip_index(df: Any) -> Any:
    import pandas as pd

    idx = df.index
    if isinstance(idx, pd.RangeIndex) and idx.start == 0 and idx.step == 1 and idx.stop == len(df):
        return df.reset_index(drop=True)
    names = [n if n is not None else ("index" if idx.nlevels == 1 else f"level_{i}")
             for i, n in enumerate(idx.names)]
    out = df.copy()
    out.index = idx.set_names(names)
    return out.reset_index()


def frame_hash(obj: Any) -> str:
    """The SHA-256 of ``obj``'s canonical form (this module's docstring)."""
    import numpy as np
    import pandas as pd

    obj = _as_frame(obj)
    if not isinstance(obj, pd.DataFrame):
        return sha256_text("json:" + canonical_json(obj))
    df = _strip_index(obj)
    names = [str(c) for c in df.columns]
    if len(set(names)) != len(names):
        dup = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"a frame with duplicate column names cannot be hashed canonically: {dup}")
    order = sorted(range(len(names)), key=lambda i: names[i])
    cols = [(names[i], *_column(df.iloc[:, i])) for i in order]
    n = len(df)
    if n > 1 and cols:
        keys = []
        for _, _, null, vals in cols:
            keys.append(null.astype("u1"))
            if vals.dtype.names:
                keys += [vals[f] for f in vals.dtype.names]
            else:
                keys.append(vals.astype(str) if vals.dtype == object else vals)
        perm = np.lexsort(list(reversed(keys)))      # lexsort's last key is the primary one
    else:
        perm = np.arange(n)
    h = hashlib.sha256()
    h.update(json.dumps([[name, kind] for name, kind, _, _ in cols], separators=(",", ":")).encode())
    h.update(b"\n" + str(n).encode() + b"\n")
    for _, kind, null, vals in cols:
        h.update(null[perm].astype("u1").tobytes())
        v = vals[perm]
        if kind == "s":
            h.update(json.dumps(v.tolist(), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        else:
            h.update(np.ascontiguousarray(v).tobytes())
    return h.hexdigest()


# --- the recorder --------------------------------------------------------------------------------------------------

_CURRENT: contextvars.ContextVar[Recorder | None] = contextvars.ContextVar("ohcamel_recorder", default=None)


def current() -> Recorder | None:
    """The recorder of the job running in this process, if any (``products.io`` records through it)."""
    return _CURRENT.get()


def _importing() -> bool:
    """True while a module is being imported: scipy.stats draws doc examples from default_rng() at import time,
    which feeds no number of any job."""
    f = sys._getframe(2)
    while f is not None:
        if f.f_code.co_filename.startswith("<frozen importlib"):
            return True
        f = f.f_back
    return False


def _regenerable(source: str) -> bool:
    return all(part.strip().startswith(REGENERABLE_PREFIXES) for part in source.split(",") if part.strip())


@dataclass
class Recorder:
    reads: list[dict[str, Any]] = field(default_factory=list)
    seeds: dict[str, int] = field(default_factory=dict)
    unseeded: list[str] = field(default_factory=list)
    unrecorded: list[str] = field(default_factory=list)
    #: regenerate.py's pins for ``products.io.previous_artifact``: lookup name -> artifact path (or None)
    replay: dict[str, str | None] | None = None

    def record(self, name: str, obj: Any, *, source: str = "warehouse") -> Any:
        self.reads.append({"name": name, "sha256": frame_hash(obj), "source": source,
                           "regenerable": _regenerable(source)})
        return obj

    def seed(self, value: Any, name: str = "seed") -> int:
        if value is None:
            raise UnseededDraw(f"{name}: the product draws random numbers but no seed was given -- a seed must "
                               "come from the job's params (or the product's fixed default), so it can be recorded")
        v = int(value)
        if name in self.seeds and self.seeds[name] != v:
            raise UnseededDraw(f"{name}: two seeds under one name ({self.seeds[name]} and {v})")
        self.seeds[name] = v
        return v

    @contextmanager
    def active(self) -> Iterator[Recorder]:
        """Install this recorder for ``products.io`` and watch for unseeded draws until the block ends."""
        import random

        import numpy as np

        token = _CURRENT.set(self)
        real_default_rng = np.random.default_rng
        np_state = np.random.get_state()
        py_state = random.getstate()
        unseeded_gens: list[tuple[Any, Any]] = []

        def default_rng(seed: Any = None) -> Any:
            g = real_default_rng(seed)
            if seed is None and not _importing():
                # judged at the end by whether it was drawn from: arch builds an unseeded placeholder it never uses
                unseeded_gens.append((g, copy.deepcopy(g.bit_generator.state)))
            return g

        np.random.default_rng = default_rng
        try:
            yield self
        finally:
            np.random.default_rng = real_default_rng
            _CURRENT.reset(token)
            drawn = sum(1 for g, st in unseeded_gens if g.bit_generator.state != st)
            if drawn:
                self.unseeded.append(f"numpy.random.default_rng() with no seed was drawn from ({drawn} generator"
                                     f"{'s' if drawn > 1 else ''})")
            after = np.random.get_state()
            if not (np_state[0] == after[0] and np.array_equal(np_state[1], after[1])
                    and np_state[2:] == after[2:]):
                self.unseeded.append("numpy.random's global state was drawn from (np.random.<dist> or a library "
                                     "using it with no random_state)")
                np.random.set_state(np_state)
            if random.getstate() != py_state:
                self.unseeded.append("the random module's global state was drawn from")
                random.setstate(py_state)

    def summary(self) -> dict[str, Any]:
        return {"reads": list(self.reads), "seed": dict(self.seeds) or None, "unseeded": list(self.unseeded),
                "unrecorded": list(self.unrecorded)}


# --- recording wrappers ----------------------------------------------------------------------------------------------

def _sql_name(sql: str, params: Any) -> str:
    text = " ".join(str(sql).split())
    return f"warehouse: {text}" + ("" if params is None else f" | {name_json(params)}")


_FETCHES = ("fetchall", "fetchone", "fetchmany", "df", "fetchdf", "fetch_df", "fetchnumpy", "arrow",
            "fetch_arrow_table", "pl")


class RecordingResult:
    """One executed statement: every fetch is recorded as a read, then returned unchanged."""

    def __init__(self, result: Any, name: str, recorder: Recorder) -> None:
        self._result = result
        self._name = name
        self._rec = recorder
        self._cols = [d[0] for d in (getattr(result, "description", None) or [])]

    def _frame(self, rows: Any) -> Any:
        import pandas as pd

        if rows is None:
            return pd.DataFrame(columns=self._cols)
        if isinstance(rows, tuple):
            rows = [rows]
        if isinstance(rows, list):
            return pd.DataFrame.from_records(rows, columns=self._cols or None)
        if isinstance(rows, dict):
            return pd.DataFrame({k: list(v) for k, v in rows.items()})
        if hasattr(rows, "to_pandas"):
            return rows.to_pandas()
        return rows

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._result, name)
        if name not in _FETCHES:
            return attr

        def fetch(*a: Any, **k: Any) -> Any:
            out = attr(*a, **k)
            self._rec.record(self._name, self._frame(out), source="warehouse")
            return out

        return fetch


class RecordingConnection:
    """``ctx.warehouse``: a read-only DuckDB connection whose every fetched result is a recorded read."""

    def __init__(self, con: Any, recorder: Recorder) -> None:
        self._con = con
        self._rec = recorder

    def execute(self, sql: str, params: Any = None) -> RecordingResult:
        res = self._con.execute(sql) if params is None else self._con.execute(sql, params)
        return RecordingResult(res, _sql_name(sql, params), self._rec)

    def close(self) -> None:
        self._con.close()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._con, name)
        if callable(attr) and not name.startswith("_"):
            self._rec.unrecorded.append(f"warehouse.{name}() bypassed the recorder")
        return attr


#: The facade's data methods: each returns a Dataset (``.data``) or an OptionChain (``.quotes``).
MARKET_READS = frozenset({"ohlcv", "prices", "returns", "quotes", "fred", "treasury_curve", "risk_free_daily",
                          "fred_metadata", "french_dataset", "ff_factors", "option_chain", "company_facts",
                          "holdings_13f", "search"})


def _sources(provenance: Any) -> str:
    out = []
    for p in provenance or []:
        s = p.get("source") if isinstance(p, dict) else getattr(p, "source", None)
        if s and s not in out:
            out.append(str(s))
    return ",".join(sorted(out)) or "unknown"


class RecordingMarket:
    """``ctx.market``: the MarketData facade, every data method's result recorded with its provenance's
    sources (``warehouse:<vendor>``, ``fixture:*``, a vendor name when the warehouse did not serve it)."""

    def __init__(self, market: Any, recorder: Recorder) -> None:
        self._market = market
        self._rec = recorder

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._market, name)
        if not callable(attr) or name.startswith("_"):
            return attr
        if name not in MARKET_READS:
            self._rec.unrecorded.append(f"market.{name}() is not a recorded read")
            return attr

        def read(*a: Any, **k: Any) -> Any:
            out = attr(*a, **k)
            data = getattr(out, "data", None)
            if data is None and hasattr(out, "quotes"):
                data = out.quotes
            args = name_json({"args": list(a), "kwargs": k})
            self._rec.record(f"market.{name}{args}", data, source=_sources(getattr(out, "provenance", None)))
            return out

        return read

