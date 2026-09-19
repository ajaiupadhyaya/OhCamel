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
would go stale on a change that judged nothing differently. Living beside
``battery/`` instead of inside it means the manifest format can evolve
without silently invalidating evidence that never depended on it.

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
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

# Fixed, repo-root-relative locations. Never absolute, and never read from a
# config file: these three are the same for every experiment and every
# strategy, so they are constants here rather than something a caller could
# get slightly wrong per call.
BATTERY_REL = Path("research/src/ohcamel_research/battery")
MACRO_REL = Path("fixtures/macro/macro.parquet")
UV_LOCK_REL = Path("research/uv.lock")


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

    ``trial_sharpe_sources`` names each source (for example, "EXP-002-SPY
    ma_crossover rerun" or "EXP-A01 wf.trial_sharpes"); it does not carry
    the Sharpe values themselves, which belong to the run that produced
    them, not to this record.

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
    trial_sharpe_sources: tuple[str, ...]
    returns_series: str


# --------------------------------------------------------------------------
# The manifest itself.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    """What one registered strategy's battery run found, and everything a
    later reader needs to tell whether that finding still holds.

    ``verdict`` is one of ``"pass"``, ``"pass, fragile"`` or ``"fail"``
    (Task 11's mechanical rule); ``verdict_line`` is the one-sentence
    version of it that Task 17 embeds directly, so no image needs
    ``report.md`` to show a strategy's evidence. ``gates`` is the charter's
    gate table -- one small JSON-safe record per gate (name, whether it
    passed, its value, and its detail, mirroring
    ``battery.gates.GateResult``) -- kept here as plain dicts rather than
    importing that dataclass, so this module's on-disk shape does not move
    every time ``battery/``'s internal representation does.

    ``selected_params`` are the parameters ``fdq``'s walk-forward chose on
    ``selection_window`` alone; Task 16's live signal is computed from
    exactly these, never a new choice. ``psr`` and ``pbo`` are kept as
    top-level floats (rather than folded into ``gates`` only) because the
    signal contract's ``validation`` block needs them as bare scalars
    (Task 12), and hunting them out of ``gates`` by name at that point would
    be a second, looser contract between the two modules.

    ``hashes`` is keyed by repo-relative POSIX path (see ``compute_hashes``)
    and is the only thing ``is_stale`` reads to decide whether this manifest
    still describes the code and data on disk.
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
    verdict: str
    verdict_line: str
    hashes: dict[str, str]
    ran_at: str
    notes: tuple[str, ...] = ()

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
                "trial_sharpe_sources": list(self.dsr.trial_sharpe_sources),
                "returns_series": self.dsr.returns_series,
            },
            "psr": self.psr,
            "pbo": self.pbo,
            "verdict": self.verdict,
            "verdict_line": self.verdict_line,
            "hashes": dict(self.hashes),
            "ran_at": self.ran_at,
            "notes": list(self.notes),
        }

    def dump(self, path: Path) -> None:
        """Write this manifest as JSON: UTF-8, sorted keys, and a NaN
        anywhere in it raises rather than being written -- a NaN in a
        manifest is a bug in whatever computed it, not a value worth
        recording."""
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
            psr=float(data["psr"]),
            pbo=float(data["pbo"]),
            verdict=data["verdict"],
            verdict_line=data["verdict_line"],
            hashes=dict(data["hashes"]),
            ran_at=data["ran_at"],
            notes=tuple(data["notes"]),
        )

    @classmethod
    def load(cls, path: Path) -> Manifest:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def manifest_path(exp_dir: Path, slug: str) -> Path:
    """``research/experiments/<EXP>/manifest.<slug>.json`` -- one manifest
    per registered strategy, never one per experiment."""
    return exp_dir / f"manifest.{slug}.json"


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
        value=float(data["value"]),
        unit=data["unit"],
        trial_count=int(data["trial_count"]),
        trial_sharpe_sources=tuple(data["trial_sharpe_sources"]),
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
    module on a different Python build."""
    battery_dir = repo_root / BATTERY_REL
    return [
        p
        for p in sorted(battery_dir.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]


def _friction_path(config_path: Path, repo_root: Path) -> Path:
    doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    try:
        rel = doc["friction"]["file"]
    except (KeyError, TypeError) as e:
        raise ValueError(f"{config_path}: no friction.file to hash") from e
    return (repo_root / rel).resolve()


def compute_hashes(
    repo_root: Path,
    config_path: Path,
    read_fixtures: Iterable[Path] = (),
) -> dict[str, str]:
    """The SHA-256 of everything a run's evidence depends on, keyed by
    repo-relative POSIX path and sorted -- deterministic regardless of
    argument order or filesystem iteration order:

    - every file under ``research/src/ohcamel_research/battery/``;
    - ``config_path`` itself (the experiment's own ``config.yaml``);
    - the friction file ``config_path`` names (``friction.file``), read from
      the config rather than passed separately, because its path is not
      knowable without the config;
    - ``fixtures/macro/macro.parquet``, read by every run for VIX widening;
    - every fixture in ``read_fixtures`` -- the caller's own list of what a
      particular run actually read (which history bars, for which symbols);
      this function does not guess that from ``repo_root`` alone;
    - ``research/uv.lock``, so a dependency change stales the evidence too.

    ``config_path`` is required rather than folded into ``read_fixtures``:
    it plays a second role (resolving the friction file's path) that a bare
    fixture never does, so giving it its own parameter says that plainly
    instead of asking a caller to remember an ordering convention.
    """
    root = repo_root.resolve()
    cfg = config_path.resolve()
    paths = [*_battery_files(root), cfg, _friction_path(cfg, root), root / MACRO_REL]
    paths.extend(Path(p).resolve() for p in read_fixtures)
    paths.append(root / UV_LOCK_REL)

    hashes: dict[str, str] = {}
    for p in paths:
        key = _repo_relative(p, root)
        hashes.setdefault(key, _hash_file(p))
    return dict(sorted(hashes.items()))


def is_stale(manifest: Manifest, repo_root: Path) -> list[str]:
    """Every reason ``manifest.hashes`` no longer describes the filesystem
    at ``repo_root``, as human-readable strings -- never a bool, so the
    Research page and the CLI can say *why* a manifest is stale rather than
    just that it is:

    - a recorded file's hash no longer matches the file;
    - a recorded file is missing entirely (covers a battery file, the
      config, the friction file, a fixture, or ``uv.lock`` having been
      deleted or moved);
    - a file ``battery/`` holds now was never recorded -- adding a file to
      the battery changes what the battery is, even though it changes no
      hash the manifest already recorded, so this case needs its own check.

    Needs no git: everything here is a filesystem read, which is all the
    image the runner produces has.
    """
    root = repo_root.resolve()
    reasons: list[str] = []
    for rel, recorded in sorted(manifest.hashes.items()):
        path = root / rel
        if not path.exists():
            reasons.append(f"{rel}: recorded in the manifest but missing now")
            continue
        current = _hash_file(path)
        if current != recorded:
            reasons.append(f"{rel}: hash no longer matches the manifest")

    battery_prefix = BATTERY_REL.as_posix() + "/"
    recorded_battery = {rel for rel in manifest.hashes if rel.startswith(battery_prefix)}
    for path in _battery_files(root):
        rel = _repo_relative(path, root)
        if rel not in recorded_battery:
            reasons.append(f"{rel}: added to battery/ since the manifest ran")
    return reasons


# --------------------------------------------------------------------------
# The runner's refusal to start on an uncommitted battery.
# --------------------------------------------------------------------------


def assert_battery_committed(repo_root: Path) -> None:
    """Refuse to let a run start if
    ``research/src/ohcamel_research/battery/`` has uncommitted changes.

    Checked at run time only, by Task 11's runner, on the checkout that is
    about to build an image or run a battery -- never inside the image
    itself, which has no ``.git`` for this to read. If ``git`` is not on the
    ``PATH`` at all, or the checkout is not a git repository, this fails
    closed: a battery this function cannot audit is refused exactly as one
    it found dirty would be, rather than being let through because the
    check itself could not run.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(BATTERY_REL)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError("git is not available; refusing to run the battery unaudited") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"git status failed: {e.stderr.strip()}") from e
    if result.stdout.strip():
        raise RuntimeError(
            "research/src/ohcamel_research/battery/ has uncommitted changes; "
            "commit it before running the battery:\n" + result.stdout
        )
