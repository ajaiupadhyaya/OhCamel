#!/usr/bin/env python3
"""Re-derive every committed battery manifest, and fail if any differs.

WHY

A manifest is trusted by its content: ``is_stale`` proves the code and data
it names are the bytes it hashed, but nothing proves the numbers in it are
what that code makes of that data. This does. For every experiment under
``research/experiments/`` that has committed manifests, it runs
``ohcamel-research battery run`` again -- on a temporary copy of the
committed tree, never on this checkout -- and compares what the run writes
with what was committed.

HOW

1. ``git archive HEAD`` of ``research/``, ``fixtures/`` and ``interface/``
   into a temporary directory, which is then made a one-commit git
   repository: the runner refuses a battery git has not committed
   (``manifest.assert_battery_committed``), and a copy of what HEAD holds is
   exactly that. Nothing in this checkout is written.
2. The committed manifests are set aside and deleted from the copy, so a
   manifest the run no longer writes cannot pass by still being there.
3. ``ohcamel-research battery run <copy>/research/experiments/<EXP>``, from
   this project's own environment with ``PYTHONPATH`` pointing at the copy's
   ``src/``: the package that runs is the copy's, so the runner's repository
   root is the copy (checked before the run, and the runner itself refuses
   an experiment outside its root). Hermetic: every ``ALPACA_``, ``APCA_``
   and ``FRED_`` variable is removed from the run's environment and every
   proxy variable points at a closed local port, so a run that reached for
   the network would fail rather than fetch. The battery reads only
   committed fixtures.
4. Each manifest written is compared with the one committed:

   - identical once ``ran_at`` is masked -- the result on the interpreter
     and platform the evidence was produced on; or else
   - identical once ``ran_at`` and ``python_version`` are masked, every
     string, integer, boolean, null, key and list length equal, and every
     float within a relative 1e-9 (absolute 1e-12 near zero). The manifests
     record ``python_version`` as a disclosure, never a staleness condition
     (manifest.py), and CI's runner is not the machine the evidence was run
     on: its interpreter differs, and its libm and SIMD kernels can move a
     float in its last bits. Every float that moved is counted and the
     largest move printed, so the difference is never silent. No change a
     reader could see in a gate, a verdict, a count, a hash or a parameter
     survives this.

   Anything else -- a manifest missing, an extra one, any other difference
   -- fails, naming the field.

USAGE

    make research-reproduce

which runs, from research/,

    uv run --locked --offline --extra dev python ../tools/reproduce_manifests.py

About 26 seconds an experiment on the machine the evidence was run on.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
TREES = ("research", "fixtures", "interface")
REL_TOL = 1e-9
ABS_TOL = 1e-12
_RAN_AT = re.compile(r'^(\s*"ran_at": )"[^"]*"', re.MULTILINE)
# Every proxy variable a Python HTTP client reads, pointed at a closed port.
_CLOSED = "http://127.0.0.1:9"
_PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_CREDENTIAL_PREFIXES = ("ALPACA_", "APCA_", "FRED_")


class Failed(Exception):
    pass


def _git(cwd: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=reproduce",
            "-c",
            "user.email=reproduce@localhost",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        check=True,
    ).stdout


def _copy_committed_tree(dest: Path) -> str:
    """HEAD's research/, fixtures/ and interface/ into ``dest``, committed
    there as one commit. Returns HEAD's SHA."""
    head = _git(REPO, "rev-parse", "HEAD").decode().strip()
    tar = _git(REPO, "archive", "--format=tar", head, "--", *TREES)
    subprocess.run(["tar", "-x", "-f", "-", "-C", str(dest)], input=tar, check=True)
    _git(dest, "init", "-q")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", f"committed tree at {head}")
    return head


def _hermetic_env(copy: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_CREDENTIAL_PREFIXES) and k not in _PROXIES
    }
    env.update(dict.fromkeys(_PROXIES, _CLOSED))
    env["NO_PROXY"] = env["no_proxy"] = ""
    env["PYTHONPATH"] = str(copy / "research" / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _require_the_copy_runs(copy: Path, env: dict[str, str]) -> None:
    where = subprocess.run(
        [sys.executable, "-c", "import ohcamel_research; print(ohcamel_research.__file__)"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not Path(where).resolve().is_relative_to(copy.resolve()):
        raise Failed(f"ohcamel_research imports from {where}, not from the copy at {copy}")


def _mask_ran_at(text: str) -> str:
    masked, n = _RAN_AT.subn(r'\1"<masked>"', text)
    if n != 1:
        raise Failed(f"expected one ran_at line, found {n}")
    return masked


def _compare(a: Any, b: Any, where: str, moved: list[float], problems: list[str]) -> None:
    """``a`` committed, ``b`` rerun. Floats within REL_TOL (recorded in
    ``moved`` when not bit-equal); everything else exactly."""
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        if a is not b:
            problems.append(f"{where}: {a!r} != {b!r}")
    elif isinstance(a, float) or isinstance(b, float):
        if not (isinstance(a, float) and isinstance(b, float)):
            problems.append(f"{where}: {a!r} != {b!r}")
        elif a != b:
            if math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL):
                moved.append(abs(a - b) / max(abs(a), abs(b)))
            else:
                problems.append(f"{where}: {a!r} != {b!r}")
    elif isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            problems.append(f"{where}: keys {sorted(set(a) ^ set(b))} differ")
        for k in sorted(set(a) & set(b)):
            _compare(a[k], b[k], f"{where}.{k}", moved, problems)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            problems.append(f"{where}: {len(a)} items != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            _compare(x, y, f"{where}[{i}]", moved, problems)
    elif type(a) is not type(b) or a != b:
        problems.append(f"{where}: {a!r} != {b!r}")


def _judge(name: str, committed: bytes, rerun: bytes) -> str:
    c_text, r_text = committed.decode("utf-8"), rerun.decode("utf-8")
    if _mask_ran_at(c_text) == _mask_ran_at(r_text):
        return "byte for byte, once ran_at is masked"
    c, r = json.loads(c_text), json.loads(r_text)
    versions = (c.pop("python_version", None), r.pop("python_version", None))
    c.pop("ran_at", None)
    r.pop("ran_at", None)
    moved: list[float] = []
    problems: list[str] = []
    _compare(c, r, "manifest", moved, problems)
    if problems:
        shown = "\n    ".join(problems[:40])
        more = f"\n    ... and {len(problems) - 40} more" if len(problems) > 40 else ""
        raise Failed(f"{name} differs from the rerun:\n    {shown}{more}")
    return (
        f"equal once ran_at and python_version (committed {versions[0]}, rerun {versions[1]}) "
        f"are masked; {len(moved)} float(s) moved in their last digits, the largest by a "
        f"relative {max(moved, default=0.0):.1e} (tolerance {REL_TOL:g})"
    )


def _reproduce(copy: Path, exp: Path, env: dict[str, str]) -> list[str]:
    committed = {p.name: p.read_bytes() for p in sorted(exp.glob("manifest.*.json"))}
    rel = exp.relative_to(copy).as_posix()
    if not committed:
        return [f"skip {rel}: no committed manifests (not run yet), nothing to reproduce"]
    for name in committed:
        (exp / name).unlink()
    cli = Path(sys.executable).with_name("ohcamel-research")
    run = subprocess.run(
        [str(cli), "battery", "run", str(exp)],
        cwd=copy,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        raise Failed(f"{rel}: the battery run failed (exit {run.returncode}):\n{run.stderr}")
    produced = {p.name: p.read_bytes() for p in sorted(exp.glob("manifest.*.json"))}
    if set(produced) != set(committed):
        raise Failed(
            f"{rel}: the run wrote {sorted(produced)}, the commit holds {sorted(committed)}"
        )
    return [
        f"ok   {rel}/{name}: {_judge(f'{rel}/{name}', committed[name], produced[name])}"
        for name in sorted(committed)
    ]


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="ohcamel-reproduce-"))
    try:
        copy = work / "repo"
        copy.mkdir()
        head = _copy_committed_tree(copy)
        env = _hermetic_env(copy)
        _require_the_copy_runs(copy, env)
        experiments = sorted(
            p
            for p in (copy / "research" / "experiments").iterdir()
            if (p / "config.yaml").is_file()
        )
        if not experiments:
            raise Failed("no research/experiments/*/config.yaml in the committed tree")
        print(f"reproducing the manifests committed at {head[:12]}")
        for exp in experiments:
            for line in _reproduce(copy, exp, env):
                print(f"  {line}")
    except Failed as e:
        print(f"FAIL {e}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("every committed manifest reproduces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
