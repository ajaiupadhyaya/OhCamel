"""Every line a verdict depends on is hashed into the manifest, because
``manifest.compute_hashes`` hashes ``battery/`` and nothing else of this
package. That holds only while nothing under ``battery/`` imports
``ohcamel_research.*`` from outside it. The one exception is
``ohcamel_research.manifest``, the judge: its verdict rule is re-checked
every time a manifest loads, so changing the rule makes every older
manifest fail to load rather than pass silently.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from ohcamel_research import REPO_ROOT
from ohcamel_research.manifest import BATTERY_REL

BATTERY = REPO_ROOT / BATTERY_REL
ALLOWED_OUTSIDE = {"ohcamel_research.manifest"}


def violations(source: str, where: str) -> list[str]:
    """Every import in ``source`` (a module under ``battery/``) that reaches
    ``ohcamel_research`` outside ``battery/``, other than the judge: an
    absolute import of any other ``ohcamel_research`` module (the package
    root included), a relative import climbing out of ``battery/``, or any
    dynamic import at all, which this check could not see through."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 1:
                found.append(f"{where}:{node.lineno}: relative import climbs out of battery/")
                continue
            if node.level == 1:
                continue  # from .x import y: inside battery/
            names = [node.module or ""]
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ("import_module", "__import__"):
                found.append(f"{where}:{node.lineno}: dynamic import")
            continue
        else:
            continue
        for n in names:
            inside = n == "ohcamel_research.battery" or n.startswith("ohcamel_research.battery.")
            ours = n == "ohcamel_research" or n.startswith("ohcamel_research.")
            if ours and not inside and n not in ALLOWED_OUTSIDE:
                found.append(f"{where}:{node.lineno}: imports {n}")
    return found


def test_battery_imports_nothing_of_this_package_from_outside_it_but_the_judge():
    files = sorted(BATTERY.rglob("*.py"))
    assert {p.name for p in files} >= {"__init__.py", "config.py", "data.py", "gates.py", "run.py"}
    found = [v for p in files for v in violations(p.read_text(), p.name)]
    assert found == []


def test_the_check_catches_every_way_out():
    assert violations("from ohcamel_research import REPO_ROOT\n", "x") == [
        "x:1: imports ohcamel_research"
    ]
    assert violations("import ohcamel_research.replay\n", "x") == [
        "x:1: imports ohcamel_research.replay"
    ]
    assert violations("from ohcamel_research.signal import wide\n", "x") == [
        "x:1: imports ohcamel_research.signal"
    ]
    assert violations("from ..contract import GATES_VERSION\n", "x") == [
        "x:1: relative import climbs out of battery/"
    ]
    assert violations("import importlib\nimportlib.import_module('m')\n", "x") == [
        "x:2: dynamic import"
    ]
    assert violations("from ohcamel_research.manifest import compute_verdict\n", "x") == []
    assert violations("from ohcamel_research.battery.gates import THRESHOLDS\n", "x") == []
    assert violations("from .gates import THRESHOLDS\n", "x") == []


def test_importing_the_runner_loads_nothing_of_this_package_outside_battery_but_the_judge():
    # In a fresh interpreter, so no other test's imports are counted.
    code = (
        "import sys, ohcamel_research.battery.run\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('ohcamel_research'))))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.split()
    outside = [
        m
        for m in out
        if m != "ohcamel_research"  # the parent package itself: always imported first
        and not m.startswith("ohcamel_research.battery")
        and m not in ALLOWED_OUTSIDE
    ]
    assert outside == []
    assert "ohcamel_research.manifest" in out


def test_the_battery_lives_where_the_manifest_hashes():
    import ohcamel_research.battery.run as run_mod

    assert Path(run_mod.__file__).resolve().parent == BATTERY.resolve()
    assert run_mod.default_repo_root() == REPO_ROOT
