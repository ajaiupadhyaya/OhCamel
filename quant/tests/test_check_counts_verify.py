"""scripts/check-counts.sh --verify: quant/README.md's TESTS must equal what pytest really collects."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check-counts.sh"


def _marker() -> int:
    return int(re.search(r"^TESTS = (\d+)$", (REPO / "quant" / "README.md").read_text(), re.M).group(1))


def _run(*args: str, collect: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    if collect is not None:
        env["CHECK_COUNTS_COLLECT_CMD"] = f"printf '%s\\n' '{collect}'"
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=REPO, env=env, capture_output=True, text=True,
                          timeout=120)


def test_verify_passes_when_the_marker_is_the_collected_count():
    n = _marker()
    r = _run("--verify", collect=f"{n}/{n + 20} tests collected (20 deselected) in 4.12s")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"quant-tests={n} (collected {n})" in r.stdout


def test_verify_fails_naming_both_numbers_when_they_differ():
    n = _marker()
    r = _run("--verify", collect=f"{n + 3} tests collected in 4.12s")
    assert r.returncode == 1
    assert f"collects {n + 3}" in r.stdout and f"TESTS = {n}" in r.stdout


def test_verify_fails_when_the_collection_cannot_be_read():
    r = _run("--verify", collect="ERROR collecting tests/test_x.py")
    assert r.returncode == 1 and "could not read" in r.stdout


def test_without_verify_pytest_is_never_run():
    r = _run(collect="garbage that would fail --verify")
    assert r.returncode == 0 and "collected" not in r.stdout
