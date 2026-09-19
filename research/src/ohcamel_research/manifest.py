"""One manifest per registered strategy: what a battery run found, and the
one place that says whether that finding still describes the code and data
it was found on.

**Why this module sits outside ``battery/``.** A manifest records SHA-256
hashes of every file under ``research/src/ohcamel_research/battery/`` (see
``compute_hashes`` below), so that a change to the gates a strategy was
judged by makes its manifest stale. If this module lived inside ``battery/``
itself, then editing *this file* -- say, adding a field to ``Manifest``, or
fixing a typo in a docstring -- would change one of the hashes every
manifest already on disk recorded, and every manifest in the repository
would go stale on a change that judged nothing differently. The module lives
at ``research/src/ohcamel_research/manifest.py``, a sibling of ``battery/``,
so the manifest format can move without the manifests it produced going
stale on their own account. This is the judge, not the dock: ``battery/``
is what gets audited, this module is what audits it, and its own protection
is its tests (``tests/test_manifest.py``), not a hash of its own bytes.

**Plan deviation, recorded here.** The design spec's File Structure lists
this module at ``battery/manifest.py``. It lives one level up instead, for
the reason just given -- putting the judge inside the dock it audits would
let editing the judge quietly revalidate (or invalidate) every verdict
already on record, which is exactly backwards.

**What must live under ``battery/`` instead, so the verdict's own code is
part of what gets hashed.** ``compute_hashes`` only ever reaches into
``battery/`` -- it does not, and must not, hash ``manifest.py``, ``cli.py``,
``replay.py``, ``signal.py``, or the Python interpreter that ran them. That
is only sound if every piece of code whose *behaviour* a verdict actually
depends on lives inside ``battery/``: Task 11's runner itself, and every
loader and slicer that runner uses to turn ``fixtures/history/*.parquet``
into the return series a gate is computed from. They do: ``load_bars``,
``slice_bars``, ``read_provenance`` and ``wide`` live in ``battery/data.py``
(``replay.py`` and ``signal.py`` import them back from there), and
``tests/test_battery_boundary.py`` fails if anything under ``battery/``
imports ``ohcamel_research.*`` from outside it, this module excepted -- so a
bug fixed in a loader cannot change what a rerun would find without moving a
hash this manifest recorded. ``python_version``
(below) is recorded for the same reason a `uv.lock` hash is: disclosed, so a
reader can see what interpreter produced a result, but never itself a
staleness condition -- the image and a workstation can legitimately run
different (sufficiently compatible) Python builds without that alone making
old evidence wrong.

**Why staleness is by content hash, not a date or a git ref.** The battery
runs inside an image built from a git checkout, and that image does not
carry a ``.git`` directory -- there is nothing for a date or a commit SHA to
be checked against once the code is baked in. A SHA-256 of the actual bytes
needs nothing but a filesystem, so ``is_stale`` below runs the same way in
the image as it does on a workstation. ``assert_battery_committed`` is the
one check in this module that *does* use git, and it is deliberately kept
out of ``is_stale``'s path: it is Task 11's runner that calls it, at the
moment a run is about to start, on a checkout that still has ``.git`` --
never inside the image, and never as part of what makes a manifest stale.

**The battery is frozen after Task 13.** Once EXP-A01 has been run for real
and its manifests committed, ``research/src/ohcamel_research/battery/`` does
not change again for the rest of this phase. Every manifest's ``hashes``
records the battery it was judged by; changing that battery afterwards -- to
fix a bug, tighten a gate, anything -- makes every existing manifest stale
by construction (this module's whole point), and a fresh run under a
changed battery is not a correction of the old evidence. It is a new
experiment, with a new id, disclosed as a second look at the question --
never a silent re-run that quietly replaces what the first run found.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from ohcamel_research.battery.gates import THRESHOLDS

# Fixed, repo-root-relative locations. Never absolute, and never read from a
# config file: both are the same for every experiment and every strategy, so
# they are constants here rather than something a caller could get slightly
# wrong per call. The friction file and the macro fixture are *not* here --
# their paths are read from each experiment's own ``config.yaml`` (see
# ``_friction_path`` and ``_macro_path``), because they can differ per
# experiment and a fixed constant would silently stop being true the day one
# did.
BATTERY_REL = Path("research/src/ohcamel_research/battery")
UV_LOCK_REL = Path("research/uv.lock")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_HASH_VALUE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_VALID_VERDICTS = ("pass", "pass, fragile", "fail")
_GATE_FIELDS = ("name", "passed", "value", "detail")


# --------------------------------------------------------------------------
# Small, JSON-shaped records nested inside a Manifest.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """A closed date range, ``start`` and ``end`` inclusive, as ISO-8601
    date strings (``"2016-06-01"``) -- the same shape ``config.yaml`` already
    uses for ``selection_window`` and ``holdout_window``, kept as strings
    rather than ``datetime.date`` so a manifest round-trips through JSON
    without a custom encoder or a lossy date/string conversion."""

    start: str
    end: str


@dataclass(frozen=True)
class DsrRecord:
    """The Deflated Sharpe Ratio, plus the three things a bare float cannot
    say for itself: what unit its trial count is in, how many trials that
    is, where their Sharpes came from, and which return series was deflated.

    ``trial_sharpe_sources`` is a map from each source's name (for example,
    "EXP-002-SPY ma_crossover rerun" or "EXP-A01 wf.trial_sharpes") to how
    many of ``trial_count``'s trials that source contributed -- never just a
    list of names, because a name on its own can't say whether forty trials
    came from the prior or four, and the two read very differently.
    ``Manifest.__post_init__`` (and so ``load``) checks that the counts sum
    to exactly ``trial_count``.

    ``returns_series`` *names* the series ``value`` was computed from (for
    example ``"walk_forward_oos+holdout"``) rather than embedding it. Task
    11 already reports the walk-forward and holdout series elsewhere in the
    manifest's ``gates`` detail; this field says which of those this
    particular DSR read, so the two never have to be reconciled by a reader
    guessing.
    """

    value: float
    unit: str
    trial_count: int
    trial_sharpe_sources: dict[str, int]
    returns_series: str


# --------------------------------------------------------------------------
# The manifest itself.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    """What one registered strategy's battery run found, and everything a
    later reader needs to tell whether that finding still holds.

    ``verdict`` is one of ``"pass"``, ``"pass, fragile"`` or ``"fail"`` and
    must equal ``compute_verdict(gates, pbo)`` -- ``__post_init__`` checks
    this on every construction, including every ``load``, so a manifest
    cannot be built or loaded with a verdict its own gates disagree with.
    ``verdict_line`` is the one-sentence version of it that Task 17 embeds
    directly, so no image needs ``report.md`` to show a strategy's evidence.
    ``gates`` is the charter's gate table -- one small JSON-safe record per
    gate, ``{name: str, passed: bool | None, value: float | None, detail:
    dict}`` (mirroring ``battery.gates.GateResult``) -- kept here as plain
    dicts rather than importing that dataclass, so this module's on-disk
    shape does not move every time ``battery/``'s internal representation
    does. ``passed`` is ``None`` for a gate that is reported but not gated
    (the cost sweep); such a gate is skipped by ``compute_verdict``, never
    counted as passing.

    ``selected_params`` are the parameters ``fdq``'s walk-forward chose on
    ``selection_window`` alone; Task 16's live signal is computed from
    exactly these, never a new choice. ``psr`` and ``pbo`` are kept as
    top-level floats (rather than folded into ``gates`` only) because the
    signal contract's ``validation`` block needs them as bare scalars
    (Task 12), and hunting them out of ``gates`` by name at that point would
    be a second, looser contract between the two modules. ``turnover`` and
    ``capacity`` are the charter's other two reported numbers
    (``battery.gates.turnover``/``capacity``); either may be ``None`` when a
    strategy's run genuinely has nothing to report (for example, a strategy
    that never traded).

    ``python_version`` (``platform.python_version()``, e.g. ``"3.13.1"``) is
    recorded so a reader can see what interpreter produced a result. It is
    never read by ``is_stale`` -- a different, compatible interpreter does
    not make old evidence wrong on its own.

    ``hashes`` is keyed by repo-relative POSIX path (see ``compute_hashes``)
    and is the only thing ``is_stale`` reads to decide whether this manifest
    still describes the code and data on disk. Every key must be relative
    (no leading ``/``, no ``..`` segment, POSIX separators) and every value
    must match ``sha256:<64 lowercase hex characters>`` -- checked by
    ``__post_init__``, the same as every other field.
    """

    experiment: str
    slug: str
    strategy: str
    symbol: str
    selected_params: dict[str, Any]
    selection_window: Window
    holdout_window: Window
    gates_version: str
    gates: tuple[dict[str, Any], ...]
    dsr: DsrRecord
    psr: float
    pbo: float
    turnover: float | None
    capacity: float | None
    verdict: str
    verdict_line: str
    hashes: dict[str, str]
    ran_at: str
    python_version: str = field(default_factory=platform.python_version)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Everything ``load`` must also enforce, run on every construction
        -- direct or via ``from_dict`` -- so ``load`` can never be looser
        than building a ``Manifest`` by hand, and so a ``dump`` can never
        have started from an object this check would have refused."""
        _require_str(self.experiment, "manifest.experiment")
        _require_str(self.slug, "manifest.slug")
        _require_str(self.strategy, "manifest.strategy")
        _require_str(self.symbol, "manifest.symbol")
        if not isinstance(self.selected_params, dict):
            raise ValueError("manifest.selected_params: expected an object")

        for w, name in (
            (self.selection_window, "selection_window"),
            (self.holdout_window, "holdout_window"),
        ):
            if not isinstance(w, Window):
                raise ValueError(f"manifest.{name}: expected a Window")
            _require_str(w.start, f"manifest.{name}.start")
            _require_str(w.end, f"manifest.{name}.end")

        _require_str(self.gates_version, "manifest.gates_version")
        if not isinstance(self.gates, tuple):
            raise ValueError("manifest.gates: expected a tuple")
        for i, g in enumerate(self.gates):
            _validate_gate(g, f"manifest.gates[{i}]")

        if not isinstance(self.dsr, DsrRecord):
            raise ValueError("manifest.dsr: expected a DsrRecord")
        _require_finite_number(self.dsr.value, "manifest.dsr.value")
        _require_str(self.dsr.unit, "manifest.dsr.unit")
        _require_int(self.dsr.trial_count, "manifest.dsr.trial_count")
        if not isinstance(self.dsr.trial_sharpe_sources, dict):
            raise ValueError("manifest.dsr.trial_sharpe_sources: expected an object")
        total = 0
        for k, v in self.dsr.trial_sharpe_sources.items():
            if not isinstance(k, str):
                raise ValueError(f"manifest.dsr.trial_sharpe_sources: key {k!r} is not a string")
            total += _require_int(v, f"manifest.dsr.trial_sharpe_sources[{k!r}]")
        if total != self.dsr.trial_count:
            raise ValueError(
                f"manifest.dsr.trial_sharpe_sources: counts sum to {total}, "
                f"trial_count is {self.dsr.trial_count}"
            )
        _require_str(self.dsr.returns_series, "manifest.dsr.returns_series")

        _require_finite_number(self.psr, "manifest.psr")
        _require_finite_number(self.pbo, "manifest.pbo")
        _require_optional_finite_number(self.turnover, "manifest.turnover")
        _require_optional_finite_number(self.capacity, "manifest.capacity")

        if self.verdict not in _VALID_VERDICTS:
            raise ValueError(f"manifest.verdict: {self.verdict!r} is not one of {_VALID_VERDICTS}")
        expected_verdict = compute_verdict(self.gates, self.pbo)
        if self.verdict != expected_verdict:
            raise ValueError(
                f"manifest.verdict: {self.verdict!r} disagrees with the mechanical rule "
                f"({expected_verdict!r}) computed from this manifest's own gates and pbo"
            )
        _validate_verdict_line(self.verdict_line, "manifest.verdict_line")

        if not isinstance(self.hashes, dict):
            raise ValueError("manifest.hashes: expected an object")
        for key, value in self.hashes.items():
            _validate_hash_key(key, "manifest.hashes")
            _validate_hash_value(value, f"manifest.hashes[{key!r}]")

        _require_str(self.ran_at, "manifest.ran_at")
        _require_str(self.python_version, "manifest.python_version")
        if not isinstance(self.notes, tuple):
            raise ValueError("manifest.notes: expected a tuple")
        for i, n in enumerate(self.notes):
            _require_str(n, f"manifest.notes[{i}]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment": self.experiment,
            "slug": self.slug,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "selected_params": dict(self.selected_params),
            "selection_window": {
                "start": self.selection_window.start,
                "end": self.selection_window.end,
            },
            "holdout_window": {"start": self.holdout_window.start, "end": self.holdout_window.end},
            "gates_version": self.gates_version,
            "gates": [dict(g) for g in self.gates],
            "dsr": {
                "value": self.dsr.value,
                "unit": self.dsr.unit,
                "trial_count": self.dsr.trial_count,
                "trial_sharpe_sources": dict(self.dsr.trial_sharpe_sources),
                "returns_series": self.dsr.returns_series,
            },
            "psr": self.psr,
            "pbo": self.pbo,
            "turnover": self.turnover,
            "capacity": self.capacity,
            "verdict": self.verdict,
            "verdict_line": self.verdict_line,
            "hashes": dict(self.hashes),
            "ran_at": self.ran_at,
            "python_version": self.python_version,
            "notes": list(self.notes),
        }

    def dump(self, path: Path) -> None:
        """Write this manifest as JSON: UTF-8, sorted keys, and a NaN
        anywhere in it raises rather than being written -- a NaN in a
        manifest is a bug in whatever computed it, not a value worth
        recording. ``sort_keys=True`` also means the file this writes never
        depends on this object's own field or dict insertion order."""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            self.to_dict(), sort_keys=True, indent=2, allow_nan=False, ensure_ascii=False
        )
        path.write_text(text + "\n", encoding="utf-8")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Manifest:
        _check_fields(data, cls, "manifest")
        return cls(
            experiment=data["experiment"],
            slug=data["slug"],
            strategy=data["strategy"],
            symbol=data["symbol"],
            selected_params=dict(data["selected_params"]),
            selection_window=_window_from_dict(
                data["selection_window"], "manifest.selection_window"
            ),
            holdout_window=_window_from_dict(data["holdout_window"], "manifest.holdout_window"),
            gates_version=data["gates_version"],
            gates=tuple(dict(g) for g in data["gates"]),
            dsr=_dsr_from_dict(data["dsr"], "manifest.dsr"),
            psr=data["psr"],
            pbo=data["pbo"],
            turnover=data["turnover"],
            capacity=data["capacity"],
            verdict=data["verdict"],
            verdict_line=data["verdict_line"],
            hashes=dict(data["hashes"]),
            ran_at=data["ran_at"],
            python_version=data["python_version"],
            notes=tuple(data["notes"]),
        )

    @classmethod
    def load(cls, path: Path) -> Manifest:
        """As strict as ``dump``: a NaN or an Infinity literal anywhere in
        the JSON text raises (``parse_constant``, below) rather than
        silently becoming a non-finite float the way ``json.loads`` would by
        default, and everything ``from_dict`` builds is then checked by
        ``Manifest.__post_init__``."""

        def _reject_non_finite(constant: str) -> float:
            raise ValueError(
                f"{path}: contains non-finite float literal {constant!r}; refusing to load"
            )

        text = path.read_text(encoding="utf-8")
        data = json.loads(text, parse_constant=_reject_non_finite)
        return cls.from_dict(data)


def manifest_path(exp_dir: Path, slug: str) -> Path:
    """``research/experiments/<EXP>/manifest.<slug>.json`` -- one manifest
    per registered strategy, never one per experiment. ``slug`` must match
    ``^[a-z0-9][a-z0-9_]*$`` -- the same shape every ``config.yaml`` strategy
    slug already uses -- so a stray path separator, a leading underscore, or
    an uppercase letter typed by hand can't turn into a manifest filename
    that lands somewhere unexpected."""
    if not _SLUG_RE.match(slug):
        raise ValueError(f"{slug!r}: slug must match {_SLUG_RE.pattern!r}")
    return exp_dir / f"manifest.{slug}.json"


# --------------------------------------------------------------------------
# Field-level validation, shared by ``Manifest.__post_init__``.
# --------------------------------------------------------------------------


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where}: expected a string, got {type(value).__name__}")
    return value


def _require_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where}: expected an int, got {type(value).__name__}")
    return value


def _require_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: expected a number, got {type(value).__name__}")
    return value


def _require_finite_number(value: Any, where: str) -> float:
    n = _require_number(value, where)
    if not math.isfinite(n):
        raise ValueError(f"{where}: must be finite, got {n!r}")
    return n


def _require_optional_finite_number(value: Any, where: str) -> None:
    if value is None:
        return
    _require_finite_number(value, where)


def _validate_gate(g: Any, where: str) -> None:
    if not isinstance(g, dict):
        raise ValueError(f"{where}: expected an object")
    missing = [k for k in _GATE_FIELDS if k not in g]
    if missing:
        raise ValueError(f"{where}: missing field {missing[0]!r}")
    unknown = sorted(set(g) - set(_GATE_FIELDS))
    if unknown:
        raise ValueError(f"{where}: unknown field {unknown[0]!r}")
    _require_str(g["name"], f"{where}.name")
    if g["passed"] is not None and not isinstance(g["passed"], bool):
        raise ValueError(f"{where}.passed: expected a bool or null")
    if g["value"] is not None:
        _require_number(g["value"], f"{where}.value")
    if not isinstance(g["detail"], dict):
        raise ValueError(f"{where}.detail: expected an object")


def _validate_verdict_line(line: Any, where: str) -> None:
    if not isinstance(line, str):
        raise ValueError(f"{where}: expected a string")
    if not line:
        raise ValueError(f"{where}: must not be empty")
    if "\n" in line or "\r" in line:
        raise ValueError(f"{where}: must be a single line (no newline characters)")
    if len(line) > 200:
        raise ValueError(f"{where}: must be at most 200 characters, got {len(line)}")


def _validate_hash_key(key: Any, where: str) -> None:
    if not isinstance(key, str):
        raise ValueError(f"{where}: hash key must be a string")
    if key.startswith("/"):
        raise ValueError(f"{where}: hash key {key!r} must be relative, not absolute")
    if "\\" in key:
        raise ValueError(f"{where}: hash key {key!r} must use POSIX separators")
    if ".." in key.split("/"):
        raise ValueError(f"{where}: hash key {key!r} must not contain a '..' segment")


def _validate_hash_value(value: Any, where: str) -> None:
    if not isinstance(value, str) or not _HASH_VALUE_RE.match(value):
        raise ValueError(
            f"{where}: hash value {value!r} does not match 'sha256:<64 lowercase hex>'"
        )


def compute_verdict(gates: Iterable[Mapping[str, Any]], pbo: float) -> str:
    """The mechanical rule: Task 11 calls this to set ``Manifest.verdict``,
    and ``Manifest.__post_init__`` (so every ``load`` too) calls it again to
    check a manifest's own ``verdict`` agrees.

    - ``"fail"`` if any gate whose ``passed`` is not ``None`` has
      ``passed is False``;
    - otherwise ``"pass, fragile"`` if ``pbo`` is over
      ``battery.gates.THRESHOLDS.pbo_fragile_above`` (the owner's EXP-A01
      ruling on fragility -- read from the same table the gate itself would,
      so the two can never drift);
    - otherwise ``"pass"``.

    A gate with ``passed is None`` (reported but not gated, e.g. the cost
    sweep) is skipped -- never counted as passing. No decided gate at all is
    ``"fail"``: a verdict with nothing behind it is not a pass. A PBO that is
    not a finite number raises -- fragility cannot be judged from it, and a
    runner that produced one has a bug, not a verdict.
    """
    if not math.isfinite(pbo):
        raise ValueError(f"compute_verdict: pbo {pbo!r} is not a finite number")
    decided = [g["passed"] for g in gates if g["passed"] is not None]
    if not decided or any(p is False for p in decided):
        return "fail"
    if pbo > THRESHOLDS.pbo_fragile_above:
        return "pass, fragile"
    return "pass"


def _field_names(cls: type) -> tuple[str, ...]:
    return tuple(f.name for f in fields(cls))


def _check_fields(data: Mapping[str, Any], cls: type, where: str) -> None:
    names = _field_names(cls)
    missing = [n for n in names if n not in data]
    if missing:
        raise ValueError(f"{where}: missing field {missing[0]!r}")
    unknown = sorted(set(data) - set(names))
    if unknown:
        raise ValueError(f"{where}: unknown field {unknown[0]!r}")


def _window_from_dict(data: Mapping[str, Any], where: str) -> Window:
    _check_fields(data, Window, where)
    return Window(start=data["start"], end=data["end"])


def _dsr_from_dict(data: Mapping[str, Any], where: str) -> DsrRecord:
    _check_fields(data, DsrRecord, where)
    return DsrRecord(
        value=data["value"],
        unit=data["unit"],
        trial_count=data["trial_count"],
        trial_sharpe_sources=dict(data["trial_sharpe_sources"]),
        returns_series=data["returns_series"],
    )


# --------------------------------------------------------------------------
# Content hashes: what makes a manifest stale.
# --------------------------------------------------------------------------


def _repo_relative(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root).as_posix()


def _hash_file(path: Path) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError as e:
        raise FileNotFoundError(f"cannot hash missing file: {path}") from e
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _battery_files(repo_root: Path) -> list[Path]:
    """Every file under ``battery/``, excluding ``__pycache__`` and
    ``*.pyc`` -- a battery run never depends on those, and including them
    would make a manifest stale every time someone merely imported the
    module on a different Python build.

    Walked with ``os.walk(..., followlinks=False)`` and refusing outright --
    never silently skipping, never silently following -- any symlink found
    anywhere under ``battery/``, file or directory. A symlink is a second
    way for ``battery/``'s effective contents to differ from what git (and
    this hash) can see, the same gap ``assert_battery_committed``'s
    tracked-file-set check exists to close; refusing here means both
    ``compute_hashes`` and ``assert_battery_committed`` (which calls this
    too) refuse a symlinked battery the same way.
    """
    battery_dir = repo_root / BATTERY_REL
    if not battery_dir.is_dir():
        return []
    result: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(battery_dir, followlinks=False):
        base = Path(dirpath)
        for d in list(dirnames):
            if d == "__pycache__":
                continue
            if (base / d).is_symlink():
                raise ValueError(f"{base / d}: symlink under battery/ is refused")
        for f in filenames:
            p = base / f
            if "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            if p.is_symlink():
                raise ValueError(f"{p}: symlink under battery/ is refused")
            result.append(p)
    return sorted(result)


def _friction_path(config_path: Path, repo_root: Path) -> Path:
    doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    try:
        rel = doc["friction"]["file"]
    except (KeyError, TypeError) as e:
        raise ValueError(f"{config_path}: no friction.file to hash") from e
    return (repo_root / rel).resolve()


def _macro_path(config_path: Path, repo_root: Path) -> Path:
    """The macro fixture's path, read from the experiment's own
    ``config.yaml`` (its ``macro:`` key) exactly as ``_friction_path`` reads
    ``friction.file`` -- never a fixed constant, so a config naming a
    different macro file is what actually gets hashed, not an assumption
    this module made on its own behalf."""
    doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    try:
        rel = doc["macro"]
    except (KeyError, TypeError) as e:
        raise ValueError(f"{config_path}: no macro: key to hash") from e
    return (repo_root / rel).resolve()


def _sidecar_path(path: Path) -> Path:
    """fdq's provenance-sidecar naming convention, exactly as
    ``fixtures/history/`` and ``ohcamel_research.replay.read_provenance``
    use it: the fixture's own full filename, extension included, with
    ``.meta.json`` appended -- ``SPY.parquet`` -> ``SPY.parquet.meta.json``,
    never the extension replaced."""
    return path.with_name(path.name + ".meta.json")


def _fixture_and_sidecar(path: Path) -> list[Path]:
    """A fixture, plus its provenance sidecar when one exists on disk. The
    sidecar is what says a bar file is real history and not synthetic
    (``replay.read_provenance``); a manifest that hashed the parquet but not
    the sidecar could go stale by nobody noticing the sidecar alone had been
    edited to claim different provenance."""
    paths = [path]
    sidecar = _sidecar_path(path)
    if sidecar.exists():
        paths.append(sidecar)
    return paths


def compute_hashes(
    repo_root: Path,
    config_path: Path,
    read_fixtures: Iterable[Path] = (),
) -> dict[str, str]:
    """The SHA-256 of everything a run's evidence depends on, keyed by
    repo-relative POSIX path and sorted -- deterministic regardless of
    argument order or filesystem iteration order:

    - every file under ``research/src/ohcamel_research/battery/`` (refusing
      any symlink found there -- see ``_battery_files``);
    - ``config_path`` itself (the experiment's own ``config.yaml``);
    - the friction file ``config_path`` names (``friction.file``);
    - the macro fixture ``config_path`` names (``macro:``), plus its
      provenance sidecar when one exists;
    - every fixture in ``read_fixtures`` -- the caller's own list of what a
      particular run actually read (which history bars, for which symbols),
      resolved against ``repo_root`` (never the current working directory)
      when given as a relative path -- plus each fixture's own provenance
      sidecar when one exists;
    - ``research/uv.lock``, so a dependency change stales the evidence too.

    ``read_fixtures`` must not be empty: a manifest that recorded no fixture
    at all would have nothing pinning it to the data a strategy was actually
    judged on, which ``is_stale`` also refuses to call fresh (see
    ``_missing_required_reasons``) -- this function refuses even earlier, at
    the point a caller would otherwise build such a manifest.

    ``config_path`` is required rather than folded into ``read_fixtures``:
    it plays two more roles (resolving the friction file's path and the
    macro fixture's path) that a bare fixture never does, so giving it its
    own parameter says that plainly instead of asking a caller to remember
    an ordering convention.
    """
    read_fixtures = list(read_fixtures)
    if not read_fixtures:
        raise ValueError(
            "compute_hashes: read_fixtures must not be empty; a manifest has to record "
            "at least one fixture the run actually read"
        )

    root = repo_root.resolve()
    cfg = config_path.resolve()
    friction_path = _friction_path(cfg, root)
    macro_path = _macro_path(cfg, root)

    paths = [*_battery_files(root), cfg, friction_path, *_fixture_and_sidecar(macro_path)]
    for p in read_fixtures:
        paths.extend(_fixture_and_sidecar((root / p).resolve()))
    paths.append(root / UV_LOCK_REL)

    hashes: dict[str, str] = {}
    for p in paths:
        key = _repo_relative(p, root)
        hashes.setdefault(key, _hash_file(p))
    return dict(sorted(hashes.items()))


def _missing_required_reasons(hashes: Mapping[str, str]) -> list[str]:
    """Every required hash category the spec names that is absent from
    ``hashes`` entirely -- never mind whether what *is* recorded still
    matches the filesystem, checked separately below. A manifest that never
    claims to have hashed its config, its friction file, its macro fixture,
    at least one history fixture, or ``research/uv.lock`` cannot be fresh no
    matter what it does record.

    These are name-pattern checks against this project's own fixed
    conventions (an experiment's own config is always named ``config.yaml``,
    a friction file's name always contains ``friction``, the macro fixture
    is always named ``macro.parquet``, and read fixtures always live under
    ``fixtures/history/``) rather than a second read of any config file --
    ``is_stale`` takes no ``config_path`` and reads no YAML, on purpose (see
    the module docstring: it is a filesystem-only check that has to run
    inside an image with no git and no access to an experiment's own
    layout beyond what the manifest itself already recorded).
    """
    reasons = []
    if UV_LOCK_REL.as_posix() not in hashes:
        reasons.append(f"{UV_LOCK_REL.as_posix()}: required key missing from the manifest")
    if not any(Path(k).name == "config.yaml" for k in hashes):
        reasons.append("config.yaml: required key missing from the manifest")
    if not any("friction" in Path(k).name for k in hashes):
        reasons.append("friction file: required key missing from the manifest")
    if not any("macro.parquet" in k for k in hashes):
        reasons.append("macro.parquet: required key missing from the manifest")
    if not any(k.startswith("fixtures/history/") for k in hashes):
        reasons.append("fixtures/history/: at least one fixture key is required, none found")
    return reasons


def is_stale(manifest: Manifest, repo_root: Path) -> list[str]:
    """Every reason ``manifest.hashes`` no longer describes the filesystem
    at ``repo_root``, as human-readable strings -- never a bool, so the
    Research page and the CLI can say *why* a manifest is stale rather than
    just that it is, and never a raise, so a manifest cannot crash the
    caller by being stale in an unusual way:

    - a required hash category (``research/uv.lock``, the config, the
      friction file, the macro fixture, at least one ``fixtures/history/``
      fixture) missing from ``manifest.hashes`` entirely
      (``_missing_required_reasons``);
    - a recorded file's hash no longer matching the file;
    - a recorded file missing entirely, or now a directory where a file was
      recorded;
    - a recorded path that raises ``OSError`` when read now (permissions, a
      broken symlink, anything else the filesystem can throw) -- reported as
      a reason, not propagated;
    - a file ``battery/`` holds now was never recorded -- adding a file to
      the battery changes what the battery is, even though it changes no
      hash the manifest already recorded, so this case needs its own check.

    Needs no git: everything here is a filesystem read, which is all the
    image the runner produces has.
    """
    root = repo_root.resolve()
    reasons: list[str] = list(_missing_required_reasons(manifest.hashes))

    for rel, recorded in sorted(manifest.hashes.items()):
        path = root / rel
        try:
            if not path.exists():
                reasons.append(f"{rel}: recorded in the manifest but missing now")
                continue
            if path.is_dir():
                reasons.append(f"{rel}: recorded as a file but is a directory now")
                continue
            current = _hash_file(path)
        except OSError as e:
            reasons.append(f"{rel}: cannot be read now ({e})")
            continue
        if current != recorded:
            reasons.append(f"{rel}: hash no longer matches the manifest")

    battery_prefix = BATTERY_REL.as_posix() + "/"
    recorded_battery = {rel for rel in manifest.hashes if rel.startswith(battery_prefix)}
    try:
        current_battery = _battery_files(root)
    except (OSError, ValueError) as e:
        reasons.append(f"{BATTERY_REL.as_posix()}: cannot be read now ({e})")
        current_battery = []
    for path in current_battery:
        try:
            rel = _repo_relative(path, root)
        except OSError:
            continue
        if rel not in recorded_battery:
            reasons.append(f"{rel}: added to battery/ since the manifest ran")
    return reasons


# --------------------------------------------------------------------------
# The runner's refusal to start on an uncommitted battery.
# --------------------------------------------------------------------------


def _status_line_is_excluded(line: str) -> bool:
    """A ``git status --porcelain`` line's path, excluded from
    ``assert_battery_committed``'s clean-tree requirement for exactly the
    two kinds of file ``compute_hashes`` also excludes -- whether the line
    reports a whole ignored directory (``!! .../battery/__pycache__/``) or
    one ignored or untracked file (``.../gates.pyc``)."""
    path = line[3:] if len(line) > 3 else ""
    return "__pycache__" in path or path.endswith(".pyc")


def _keep_for_hash_comparison(path: str) -> bool:
    p = Path(path)
    return "__pycache__" not in p.parts and p.suffix != ".pyc"


def assert_battery_committed(repo_root: Path) -> None:
    """Refuse to let a run start unless
    ``research/src/ohcamel_research/battery/`` is exactly what git has
    committed -- nothing missing, nothing extra, nothing merely staged, and
    nothing git would hide from a plain ``git status``.

    Checked at run time only, by Task 11's runner, on the checkout that is
    about to build an image or run a battery -- never inside the image
    itself, which has no ``.git`` for this to read. Every failure here fails
    closed: a battery this function cannot fully audit is refused exactly as
    one it found dirty would be.

    Specifically, in order:

    1. ``battery/`` must exist as a directory at all.
    2. ``repo_root`` must be inside a git working tree, and ``git`` must be
       on ``PATH``.
    3. ``git status --porcelain --untracked-files=all --ignored=matching --
       <battery>`` must print nothing, once lines naming only
       ``__pycache__`` or a ``*.pyc`` file are set aside. Plain
       ``git status --porcelain`` (no ``--ignored``) would miss a file a
       ``.gitignore`` hides -- a ``.DS_Store`` under ``battery/`` would then
       get hashed into every manifest while being invisible to git, which
       defeats the whole point of this check. ``--untracked-files=all``
       likewise stops an untracked *directory* from being collapsed into one
       line that this function's path-based exclusion could not see through.
    4. The set of files ``compute_hashes`` would hash under ``battery/``
       must equal ``git ls-files -- <battery>``, filtered to the same
       ``__pycache__``/``*.pyc`` exclusion -- a second, independent way of
       saying "what git tracks is what gets hashed," in case a future
       git-status flag or ``.gitattributes`` rule ever let the two drift
       without tripping step 3.

    This function never sets ``is_stale``'s verdict and is never called from
    it: it is a run-time precondition, not a staleness rule, and it is the
    one thing in this module that talks to git.
    """
    root = repo_root.resolve()
    battery_dir = root / BATTERY_REL

    if not battery_dir.is_dir():
        raise RuntimeError(
            f"{BATTERY_REL.as_posix()}: does not exist; refusing to run an unaudited battery"
        )

    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, check=True
            )
        except FileNotFoundError as e:
            raise RuntimeError("git is not available; refusing to run the battery unaudited") from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"{root}: git {' '.join(args)} failed ({e.stderr.strip()}); "
                "refusing to run the battery unaudited"
            ) from e

    _git("rev-parse", "--is-inside-work-tree")

    status = _git(
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--ignored=matching",
        "--",
        str(BATTERY_REL),
    )
    dirty = [line for line in status.stdout.splitlines() if not _status_line_is_excluded(line)]
    if dirty:
        raise RuntimeError(
            f"{BATTERY_REL.as_posix()} is not clean (uncommitted, staged, untracked, or "
            "ignored-but-present changes); commit or remove before running the battery:\n"
            + "\n".join(dirty)
        )

    tracked = {
        p
        for p in _git("ls-files", "--", str(BATTERY_REL)).stdout.splitlines()
        if _keep_for_hash_comparison(p)
    }
    hashed = {_repo_relative(p, root) for p in _battery_files(root)}
    if tracked != hashed:
        raise RuntimeError(
            f"{BATTERY_REL.as_posix()}'s tracked files disagree with what compute_hashes would "
            f"hash: hashed-but-not-tracked={sorted(hashed - tracked)}, "
            f"tracked-but-not-hashed={sorted(tracked - hashed)}"
        )
