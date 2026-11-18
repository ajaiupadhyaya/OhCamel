"""Make an artifact's numbers again from a warehouse export, and say whether they came out the same (Task 3.3).

    tools/regenerate.py <artifact dir> --warehouse <export dir> [--artifacts ROOT] [--allow-drift] [--scratch DIR]

In order, refusing loudly at the first thing that would make the comparison unfaithful:

1. The kind: ``provenance.NOT_REGENERABLE`` (ingests, the signal producers, the desk report, Kronos, the
   self-test) is refused with its reason (exit 2).
2. The reads: a manifest whose ``data_hash`` is null (a read went unrecorded) is refused with its note, and so
   is one with a read no export holds -- a vendor's answer, the live engine -- each named (exit 2). A lookup of an
   earlier artifact is pinned to the artifact the original run found; it must be under ``--artifacts`` (default:
   the root the artifact itself sits in), read-only.
3. The code: the checkout's git SHA, quant/uv.lock's hash and the kernels' engine must equal the manifest's
   (exit 3); ``--allow-drift`` proceeds with a WARNING per difference. The re-run uses the manifest's threads.
4. The run: the export is imported into a NEW DuckDB file in a scratch directory (never the live warehouse), the
   job runs with the manifest's kind, params and threads, offline, its clock pinned at the manifest's
   ``started_at``, its data directory in the scratch, its tables written to the scratch. Nothing is written into
   the artifact store, the export or the live warehouse.
5. The data: the re-run's ``data_hash`` must equal the manifest's; if not, the reads that differ are named and
   no table is compared -- the export does not hold the data the job read (exit 4).
6. The tables: every table, every column, equal; floats within 1e-9 (absolute, or relative to the larger
   magnitude). Wall-clock stamp columns (``provenance.RUN_STAMPS``) are masked and named. A mismatch prints the
   table, the column, how many rows differ, the first rows and the largest difference (exit 1).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from . import provenance as P

TOL = 1e-9
SHOW_ROWS = 5


class Refused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


checkout_sha = P.checkout_sha


def _say(msg: str) -> None:
    print(f"regenerate: {msg}", flush=True)


def _check_kind_and_reads(m: dict[str, Any]) -> None:
    kind = m["kind"]
    if kind in P.NOT_REGENERABLE:
        raise Refused(2, f"{kind} cannot be regenerated: {P.NOT_REGENERABLE[kind]}")
    if not m.get("data_hash"):
        raise Refused(2, f"the manifest names no data_hash, so the data a re-run reads cannot be checked: "
                         f"{m.get('data_hash_note') or 'the manifest predates Task 3.3'}")
    live = [r for r in m.get("reads") or [] if not r.get("regenerable")]
    if live:
        raise Refused(2, "the job read data no export holds, so a re-run cannot read it again:\n"
                      + "\n".join(f"  - {r['name'][:160]}  (source: {r['source']})" for r in live))


def _check_code(m: dict[str, Any], allow: bool) -> None:
    here = {"code_sha": checkout_sha(), "lock_hash": P.lock_hash(), "kernels": P.kernels_info()}
    drift = []
    if m.get("code_sha") != here["code_sha"]:
        drift.append(f"code: the artifact was made by {m.get('code_sha')}, this checkout is {here['code_sha']}")
    if m.get("lock_hash") != here["lock_hash"]:
        drift.append(f"lock: quant/uv.lock hashes {here['lock_hash']}, the manifest says {m.get('lock_hash')}")
    want = (m.get("kernels") or {}).get("engine")
    if want and want != here["kernels"]["engine"]:
        drift.append(f"kernels: the artifact ran on {want}, this process runs {here['kernels']['engine']} "
                     "(random kernels agree across engines only in distribution)")
    elif want == "rust" and (m.get("kernels") or {}).get("api") != here["kernels"]["api"]:
        drift.append(f"kernels: wheel API {m['kernels'].get('api')} then, {here['kernels']['api']} now")
    if not drift:
        _say(f"code {here['code_sha'][:12]}, lock {str(here['lock_hash'])[:12]}, kernels {here['kernels']['engine']}: "
             "as the manifest names them")
        return
    if not allow:
        raise Refused(3, "this checkout is not what made the artifact:\n" + "\n".join(f"  - {d}" for d in drift)
                      + "\n  pass --allow-drift to re-run anyway (each difference is then a WARNING)")
    for d in drift:
        _say(f"WARNING {d}")


def _replay(m: dict[str, Any], artifacts_root: Path) -> dict[str, str | None]:
    pins: dict[str, str | None] = {}
    for r in m.get("reads") or []:
        if not r["name"].startswith("artifact-lookup: "):
            continue
        rel = r.get("artifact")
        if rel is None:
            pins[r["name"]] = None
            continue
        path = artifacts_root / rel
        if not (path / "manifest.json").is_file():
            raise Refused(2, f"the job read the earlier artifact {rel}, which is not under {artifacts_root} "
                             "(pruned, or another root): pass --artifacts <root> holding it")
        pins[r["name"]] = str(path)
    return pins


def _diff_reads(want: list[dict[str, Any]], got: list[dict[str, Any]]) -> list[str]:
    from collections import Counter

    w = Counter((r["name"], r["sha256"]) for r in want)
    g = Counter((r["name"], r["sha256"]) for r in got)
    names_w = {r["name"] for r in want}
    names_g = {r["name"] for r in got}
    lines = []
    for name in sorted(names_w | names_g):
        if name not in names_g:
            lines.append(f"  - not read again: {name[:200]}")
        elif name not in names_w:
            lines.append(f"  - read only now: {name[:200]}")
        elif {k: v for k, v in w.items() if k[0] == name} != {k: v for k, v in g.items() if k[0] == name}:
            lines.append(f"  - different rows: {name[:200]}")
    return lines


def _num(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def compare_table(name: str, want: pd.DataFrame, got: pd.DataFrame, masked: frozenset[str]) -> list[str]:
    """Readable differences between two tables (empty when they match)."""
    out = []
    if list(want.columns) != list(got.columns):
        return [f"{name}: columns differ -- artifact {list(want.columns)}, re-run {list(got.columns)}"]
    if len(want) != len(got):
        return [f"{name}: {len(want)} rows in the artifact, {len(got)} in the re-run"]
    for col in want.columns:
        if col in masked:
            continue
        a, b = want[col].reset_index(drop=True), got[col].reset_index(drop=True)
        if _num(a) and _num(b):
            x, y = a.to_numpy(dtype="float64", na_value=np.nan), b.to_numpy(dtype="float64", na_value=np.nan)
            both_nan = np.isnan(x) & np.isnan(y)
            with np.errstate(invalid="ignore"):
                d = np.abs(x - y)
                scale = np.maximum(1.0, np.maximum(np.abs(x), np.abs(y)))
                bad = ~both_nan & ~((x == y) | (d <= TOL * scale))
            if bad.any():
                dd = np.where(bad, np.nan_to_num(d, nan=np.inf), 0.0)
                rows = np.flatnonzero(bad)[:SHOW_ROWS]
                out.append(f"{name}.{col}: {int(bad.sum())} of {len(x)} rows differ beyond {TOL:g}; largest "
                           f"difference {float(dd.max()):.6g} (row {int(dd.argmax())}); first rows: "
                           + ", ".join(f"[{i}] {float(x[i])!r} -> {float(y[i])!r}" for i in rows))
        else:
            sa, sb = a.astype(object).where(a.notna(), None), b.astype(object).where(b.notna(), None)
            bad = np.array([not (u is None and v is None) and not _same(u, v) for u, v in zip(sa, sb, strict=True)])
            if bad.any():
                rows = np.flatnonzero(bad)[:SHOW_ROWS]
                out.append(f"{name}.{col}: {int(bad.sum())} of {len(sa)} rows differ; first rows: "
                           + ", ".join(f"[{i}] {sa[i]!r} -> {sb[i]!r}" for i in rows))
    return out


def _same(u: Any, v: Any) -> bool:
    try:
        r = u == v
        return bool(r) if not hasattr(r, "all") else bool(np.asarray(r).all())
    except (TypeError, ValueError):
        return P.canonical_json(u) == P.canonical_json(v)


def regenerate(artifact: Path, export: Path, *, artifacts_root: Path | None = None, allow_drift: bool = False,
               scratch: Path | None = None) -> int:
    from ..warehouse.export import ExportFailed, read_record, restore_export
    from .artifacts import read_reads
    from .local import execute, make_context, pinned_clock

    artifact = Path(artifact).resolve()
    m = json.loads((artifact / "manifest.json").read_text())
    m["reads"] = read_reads(artifact)
    _say(f"{m['kind']} {m['id']} (started {m.get('started_at')}, data_hash {str(m.get('data_hash'))[:16]}..., "
         f"seed {m.get('seed')}, threads {m.get('threads')})")
    _check_kind_and_reads(m)
    _check_code(m, allow_drift)
    root = Path(artifacts_root).resolve() if artifacts_root else artifact.parent.parent
    pins = _replay(m, root)
    try:
        rec = read_record(export)
    except ExportFailed as e:
        raise Refused(2, str(e)) from e
    _say(f"export {export} of {rec['day']} (schema version {rec['schema_version']}, "
         f"{sum(rec['tables'].values())} rows)")
    own = scratch is None
    work = Path(tempfile.mkdtemp(prefix="ohcamel-regenerate-")) if own else Path(scratch)
    if work.resolve().is_relative_to(root) or work.resolve().is_relative_to(Path(export).resolve()):
        raise Refused(2, f"the scratch directory {work} is inside the artifact store or the export")
    try:
        wh = restore_export(export, work / "warehouse.duckdb")
        settings = Settings(warehouse_path=wh, offline=True, data_dir=work / "data", signals_dir=None)
        ctx = make_context(dict(m["params"]), settings=settings, threads=int(m.get("threads") or 1),
                           job_id=f"regenerate-{m['id']}")
        ctx.recorder.replay = pins
        try:
            with pinned_clock(m.get("started_at")):
                result = execute(m["kind"], dict(m["params"]), ctx, work / "out")
        finally:
            ctx.close()
        got_hash = P.data_hash(result["reads"]) if not P.unrecorded_note(m["kind"], result) else None
        if got_hash != m["data_hash"]:
            lines = _diff_reads(m["reads"], result["reads"])
            _say("data_hash differs: the export does not hold the data the job read (no table was compared)\n"
                 + "\n".join(lines or ["  - (the reads are the same; an unrecorded read appeared: "
                                       f"{P.unrecorded_note(m['kind'], result)})"]))
            return 4
        _say(f"data_hash matches ({len(result['reads'])} reads)")
        if (result.get("seed") or None) != (m.get("seed") or None):
            _say(f"seed differs: {m.get('seed')} then, {result.get('seed')} now")
            return 1
        masked = P.RUN_STAMPS.get(m["kind"], frozenset())
        problems = []
        if sorted(result["tables"]) != sorted(m["tables"]):
            problems.append(f"tables differ: artifact {sorted(m['tables'])}, re-run {sorted(result['tables'])}")
        for t in m["tables"]:
            if t not in result["tables"]:
                continue
            diff = compare_table(t, pd.read_parquet(artifact / f"{t}.parquet"), pd.read_parquet(work / "out" / f"{t}.parquet"),
                                 masked)
            problems += diff
            _say(f"  {t}: {'DIFFERS' if diff else 'matches'}")
        if masked:
            _say(f"masked as wall-clock stamps: {', '.join(sorted(masked))}")
        if problems:
            _say("MISMATCH\n" + "\n".join(f"  - {p}" for p in problems))
            return 1
        _say(f"every table matches ({len(m['tables'])} tables, floats within {TOL:g})")
        return 0
    finally:
        if own:
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools/regenerate.py", description=__doc__.split("\n\n")[0])
    p.add_argument("artifact", type=Path, help="the artifact directory (<root>/<kind>/<id>, with manifest.json)")
    p.add_argument("--warehouse", type=Path, required=True, help="a verified warehouse export directory")
    p.add_argument("--artifacts", type=Path, default=None,
                   help="the artifact root holding earlier artifacts the job read (default: the artifact's own root)")
    p.add_argument("--allow-drift", action="store_true",
                   help="re-run although the code SHA, lock or kernels differ from the manifest's")
    p.add_argument("--scratch", type=Path, default=None, help="work here instead of a temporary directory")
    a = p.parse_args(argv)
    try:
        return regenerate(a.artifact, a.warehouse, artifacts_root=a.artifacts, allow_drift=a.allow_drift,
                          scratch=a.scratch)
    except Refused as e:
        print(f"regenerate: REFUSED -- {e}", file=sys.stderr, flush=True)
        return e.code


if __name__ == "__main__":
    raise SystemExit(main())
