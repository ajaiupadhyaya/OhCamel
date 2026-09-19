"""Emit a signal from an fdq strategy, in the contract's shape.

Point-in-time by construction: the strategy is handed only bars dated on or
before ``as_of``, and ``data_hash`` is computed over exactly that frame, so
the hash is a statement about what the strategy could have seen.

fdq's long/flat strategies are state machines (``should_rebalance`` flips a
pending state on a crossover), so a single call at ``as_of`` would not
reproduce what a daily run would have held. The machine is therefore walked
forward over every bar date up to ``as_of``, which is what a daily process
would have done, and the weights read at the end.

**The document's strategy slug and fdq's strategy key are two different
things**, and ``emit`` takes them as two different arguments. The slug
(``strategy`` below) is what the schema calls ``strategy``: the name the
desk has registered limits and a manifest for (``exp_a01_spy``,
``exp_a01_tlt``). The fdq key (``fdq_strategy``) is which entry of
``REGISTRY`` computes the weights (``ma_crossover``, ``donchian``). Two
strategies on the same fdq rule but different symbols -- EXP-A01's SPY and
TLT -- share an fdq key and must not share a slug: a single argument doing
both jobs would make both documents say ``"strategy": "ma_crossover"``,
colliding on file name, on sequence, and on the desk's R5/R2 bookkeeping,
which is keyed by the slug alone.

**The validation block comes only from a manifest, and ``emit`` never takes
one as a dict.** ``validation_from_manifest`` is the one function that may
set ``status`` to anything but ``"unvalidated"``, and it does so only by
reading a fresh manifest whose own verdict says so (see its docstring).
``emit`` takes ``validation_from``, a path to a manifest (or ``None``), and
calls ``validation_from_manifest`` on it itself; there is no parameter here
that accepts a ready-made ``dict``. This is not merely a CLI-level
convention -- Task 16's research service calls ``emit`` directly, in
Python, where a CLI's refusal to expose a ``--status`` flag would not have
reached at all -- so the refusal has to live in this function's own
signature. Passing anything but a path (or ``None``) as ``validation_from``,
by keyword or by position, is a ``TypeError``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fdq.strategies.trend import Donchian, MACrossover

from ohcamel_research import REPO_ROOT
from ohcamel_research.battery.data import wide
from ohcamel_research.contract import GATES_VERSION, check, data_hash, params_hash
from ohcamel_research.manifest import Manifest, is_stale

__all__ = [
    "REGISTRY",
    "UNVALIDATED",
    "emit",
    "validation_from_manifest",
    "wide",
    "write_signal",
]

REGISTRY: dict[str, type] = {"ma_crossover": MACrossover, "donchian": Donchian}

UNVALIDATED: dict[str, Any] = {
    "status": "unvalidated",
    "gates_version": GATES_VERSION,
    "dsr": None,
    "psr": None,
    "pbo": None,
    "manifest": None,
}

_PASSING_VERDICTS = ("pass", "pass, fragile")


def validation_from_manifest(manifest_path: Path, repo_root: Path) -> dict[str, Any]:
    """The signal's ``validation`` block -- and the *only* way to get a
    ``status`` other than ``"unvalidated"``.

    - ``"pass"`` only when the manifest at ``manifest_path`` loads, is fresh
      (``manifest.is_stale`` returns no reasons against ``repo_root``), and
      its own ``verdict`` is ``"pass"`` or ``"pass, fragile"``.
    - ``"fail"`` when the manifest loads and is fresh but its verdict is
      ``"fail"``.
    - ``"unvalidated"`` in every other case: the manifest file is missing,
      its JSON is malformed, it fails its own field validation
      (``Manifest.__post_init__``), or it loads but is stale. This function
      never raises for any of those -- a caller that always routes
      ``validation`` through here can never end up emitting a hand-written
      ``"pass"``, because there is no path through it that produces one
      except an actually-fresh, actually-passing manifest.

    The schema (``interface/signal.schema.json``) has no field of its own
    for *why* validation did not produce a pass, and this task does not
    change the schema. The reason -- unreadable, malformed, or the list of
    staleness reasons from ``is_stale`` -- is recorded as a suffix on the
    ``manifest`` field, which the schema already allows as a free-form
    string ("path or content hash of the battery manifest this block was
    written from"). When the manifest does load, ``manifest`` is simply
    ``str(manifest_path)``, matching that description exactly; the suffix
    is added only when there is a reason to explain.
    """
    manifest_str = str(manifest_path)
    try:
        manifest = Manifest.load(manifest_path)
    except (OSError, ValueError) as e:
        return {
            "status": "unvalidated",
            "gates_version": GATES_VERSION,
            "dsr": None,
            "psr": None,
            "pbo": None,
            "manifest": f"{manifest_str}: cannot be loaded ({e})",
        }

    reasons = is_stale(manifest, repo_root)
    if reasons:
        return {
            "status": "unvalidated",
            "gates_version": manifest.gates_version,
            "dsr": manifest.dsr.value,
            "psr": manifest.psr,
            "pbo": manifest.pbo,
            "manifest": f"{manifest_str}: stale ({'; '.join(reasons)})",
        }

    status = "pass" if manifest.verdict in _PASSING_VERDICTS else "fail"
    return {
        "status": status,
        "gates_version": manifest.gates_version,
        "dsr": manifest.dsr.value,
        "psr": manifest.psr,
        "pbo": manifest.pbo,
        "manifest": manifest_str,
    }


def emit(
    strategy: str,
    fdq_strategy: str,
    params: dict[str, Any],
    as_of: date,
    bars_long: pd.DataFrame,
    sequence: int,
    validation_from: Path | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Emit one signal document. See the module docstring for ``strategy``
    vs. ``fdq_strategy``.

    ``validation_from`` is a path to a battery manifest, or ``None``. When
    given, the validation block is built by calling
    ``validation_from_manifest(validation_from, repo_root)`` right here, so
    it can only ever be ``pass``, ``fail`` or ``unvalidated`` by that
    function's own rules. When ``None``, the document is unvalidated
    (``UNVALIDATED``). There has never been, and is not now, any argument
    that accepts a ready-made validation ``dict`` -- passing one (by
    keyword, where it lands on no parameter at all, or positionally, where
    it would land on ``validation_from``) is refused with ``TypeError``.
    ``repo_root`` defaults to this repository's own root so most callers
    never pass it; it exists as a parameter so a caller -- the research
    service, a test -- can validate a manifest against a filesystem other
    than the one this package lives in.
    """
    if validation_from is not None and not isinstance(validation_from, (str, Path)):
        raise TypeError(
            "emit: validation_from must be a path to a manifest, or None -- "
            f"not {type(validation_from).__name__}; there is no way to hand emit() "
            "a ready-made validation dict"
        )
    if fdq_strategy not in REGISTRY:
        raise KeyError(f"unknown fdq strategy {fdq_strategy!r}; known: {sorted(REGISTRY)}")
    hist = bars_long.loc[bars_long["date"] <= as_of].reset_index(drop=True)
    if hist.empty:
        raise ValueError(f"no bars on or before {as_of}")
    strat = REGISTRY[fdq_strategy](dict(params))
    w = wide(hist)
    for d in sorted(hist["date"].unique()):
        strat.should_rebalance(d, w)
    weights = strat.target_weights(as_of, w)
    targets = [
        {"symbol": str(sym), "weight": float(x)} for sym, x in weights.items() if float(x) != 0.0
    ]
    validation = (
        validation_from_manifest(Path(validation_from), repo_root)
        if validation_from is not None
        else dict(UNVALIDATED)
    )
    doc = {
        "schema_version": 1,
        "strategy": strategy,
        "params_hash": params_hash(params),
        "as_of": as_of.isoformat(),
        "computed_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "data_hash": data_hash(hist),
        "sequence": int(sequence),
        "validation": validation,
        "targets": targets,
    }
    problems = check(doc)
    if problems:
        raise ValueError("refusing to emit an invalid signal: " + "; ".join(problems))
    return doc


def write_signal(doc: dict[str, Any], out: Path) -> None:
    """Write a signal document atomically, so no reader can ever observe a
    half-written file under a ``*.json`` name (the intake records a
    malformed file once and never retries it -- a partial write would be
    permanently unreadable, not merely late).

    The temp file is created in ``out``'s own directory -- so the rename
    is same-filesystem and therefore atomic -- and named so it never ends
    in ``.json``: ``os.replace`` is what makes the final name appear, and
    until that call nothing named ``*.json`` exists at all. ``allow_nan``
    is set to ``False`` explicitly (``json.dumps``'s own default is
    ``True``, which would silently write the non-standard ``NaN`` token):
    a NaN weight is a bug in whatever computed it, and a document that
    cannot be written cannot be emitted, unvalidated or otherwise.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2, allow_nan=False) + "\n"
    tmp = out.with_name(f".{out.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
