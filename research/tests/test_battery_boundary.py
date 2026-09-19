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


# Machinery that can load or run code this AST scan could not see through.
DYNAMIC_MODULES = ("importlib", "runpy", "pkgutil")
DYNAMIC_CALLS = ("exec", "eval", "__import__", "import_module")


def _dynamic_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in DYNAMIC_MODULES)


def violations(source: str, where: str) -> list[str]:
    """Every way ``source`` (a module under ``battery/``) could reach code
    outside ``battery/`` other than the judge:
    - an absolute import of any other ``ohcamel_research`` module (the
      package root included);
    - a relative import climbing out of ``battery/``;
    - dynamic loading this scan cannot see through: importing ``importlib``
      (``importlib.util`` included), ``runpy`` or ``pkgutil``, or naming
      ``pkgutil`` at all; calling ``exec``, ``eval``, ``__import__`` or
      ``import_module``; touching ``sys.modules`` under any name ``sys`` is
      bound to (``import sys as s; s.modules``, ``from sys import modules``);
      ``getattr`` on ``sys`` or ``builtins`` (or ``__builtins__``), under any
      alias; importing ``getattr`` or any of those calls from ``builtins``."""
    tree = ast.parse(source)
    sys_names: set[str] = set()
    builtins_names: set[str] = {"__builtins__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "sys":
                    sys_names.add(a.asname or "sys")
                if a.name == "builtins":
                    builtins_names.add(a.asname or "builtins")
    guarded = sys_names | builtins_names

    found = []
    for node in ast.walk(tree):
        line = f"{where}:{getattr(node, 'lineno', 0)}"
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 1:
                found.append(f"{line}: relative import climbs out of battery/")
                continue
            if node.level == 1:
                continue  # from .x import y: inside battery/
            names = [node.module or ""]
            imported = {a.name for a in node.names}
            if node.module == "sys" and imported & {"modules", "*"}:
                found.append(f"{line}: sys.modules")
            if node.module == "builtins" and imported & {"getattr", "*", *DYNAMIC_CALLS}:
                found.append(f"{line}: dynamic code (from builtins)")
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in DYNAMIC_CALLS:
                found.append(f"{line}: dynamic code ({name})")
            if (
                name == "getattr"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in guarded
            ):
                found.append(f"{line}: getattr on {node.args[0].id}")
            continue
        elif isinstance(node, ast.Attribute):
            if node.attr == "modules" and getattr(node.value, "id", None) in sys_names:
                found.append(f"{line}: sys.modules")
            continue
        elif isinstance(node, ast.Name):
            if node.id == "pkgutil":
                found.append(f"{line}: dynamic import machinery (pkgutil)")
            continue
        else:
            continue
        for n in names:
            if _dynamic_module(n):
                found.append(f"{line}: dynamic import machinery ({n})")
                continue
            inside = n == "ohcamel_research.battery" or n.startswith("ohcamel_research.battery.")
            ours = n == "ohcamel_research" or n.startswith("ohcamel_research.")
            if ours and not inside and n not in ALLOWED_OUTSIDE:
                found.append(f"{line}: imports {n}")
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
        "x:1: dynamic import machinery (importlib)",
        "x:2: dynamic code (import_module)",
    ]
    assert violations("import importlib.util\n", "x") == [
        "x:1: dynamic import machinery (importlib.util)"
    ]
    assert violations("from importlib import util\n", "x") == [
        "x:1: dynamic import machinery (importlib)"
    ]
    assert violations("from importlib.util import spec_from_file_location\n", "x") == [
        "x:1: dynamic import machinery (importlib.util)"
    ]
    assert violations("import runpy\n", "x") == ["x:1: dynamic import machinery (runpy)"]
    assert violations("exec('x = 1')\n", "x") == ["x:1: dynamic code (exec)"]
    assert violations("y = eval('1')\n", "x") == ["x:1: dynamic code (eval)"]
    assert violations("m = __import__('os')\n", "x") == ["x:1: dynamic code (__import__)"]
    assert violations("import sys\nm = sys.modules['x']\n", "x") == ["x:2: sys.modules"]
    assert violations("from sys import modules\n", "x") == ["x:1: sys.modules"]
    assert violations("import sys as s\nm = s.modules\n", "x") == ["x:2: sys.modules"]
    assert violations("import sys as s\nm = getattr(s, 'modules')\n", "x") == ["x:2: getattr on s"]
    assert violations("import sys\nm = getattr(sys, 'modules')\n", "x") == ["x:2: getattr on sys"]
    assert violations("import builtins as b\nf = getattr(b, 'ev' + 'al')\n", "x") == [
        "x:2: getattr on b"
    ]
    assert violations("f = getattr(__builtins__, 'exec')\n", "x") == [
        "x:1: getattr on __builtins__"
    ]
    assert violations("from builtins import getattr as g\n", "x") == [
        "x:1: dynamic code (from builtins)"
    ]
    assert violations("import pkgutil\n", "x") == ["x:1: dynamic import machinery (pkgutil)"]
    assert violations("from pkgutil import iter_modules\n", "x") == [
        "x:1: dynamic import machinery (pkgutil)"
    ]
    assert violations("x = pkgutil.iter_modules()\n", "x") == [
        "x:1: dynamic import machinery (pkgutil)"
    ]
    # Ordinary attribute access and getattr on anything else pass.
    assert violations("import sys\nv = sys.version_info\n", "x") == []
    assert violations("v = getattr(obj, 'modules')\n", "x") == []
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
