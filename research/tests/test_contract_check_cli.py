"""``contract check --core`` skips rows marked ``pending-task-22``, and says so."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from ohcamel_research import REPO_ROOT, cli

REAL = REPO_ROOT / "interface" / "examples"


def test_a_pending_row_is_skipped_by_the_core_side_and_reported(tmp_path: Path, monkeypatch):
    examples = tmp_path / "examples"
    examples.mkdir()
    shutil.copy(REAL / "v2_pass.json", examples / "v2_pass.json")
    real = json.loads((REAL / "expected.json").read_text())
    (examples / "expected.json").write_text(
        json.dumps({**real, "cases": {"v2_pass.json": real["cases"]["v2_pass.json"]}})
    )
    # A core that fails loudly if it is ever asked about the row.
    core = tmp_path / "core"
    core.write_text("#!/bin/sh\necho 'REJECT R1: the core must not be asked'\n")
    core.chmod(0o755)
    monkeypatch.setattr(cli, "EXAMPLES", examples)

    result = CliRunner().invoke(cli.cli, ["contract", "check", "--core", str(core)])

    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert "1 row(s) skipped on the core side (pending-task-22)" in result.output
