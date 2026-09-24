"""``ohcamel-research fixtures doctor`` against the committed ten-year history.

Task 8 vendored the nine-ETF history (``fixtures/history/``) and the FRED
macro series (``fixtures/macro/``), each with an fdq provenance sidecar. The
doctor command is Alpha's own fixture check (``fixtures.py`` there); this
test is the thing ``make research-test``'s two extra doctor lines exercise,
kept in pytest too so a broken fixture fails the ordinary suite, not only a
Makefile line a contributor might skip.
"""

from __future__ import annotations

from click.testing import CliRunner

from ohcamel_research import REPO_ROOT
from ohcamel_research.cli import cli

HISTORY = REPO_ROOT / "fixtures" / "history"
MACRO = REPO_ROOT / "fixtures" / "macro"

NINE_ETFS = ["GLD", "IEF", "IWM", "QQQ", "SPY", "TLT", "XLE", "XLF", "XLK"]


def test_doctor_accepts_the_ten_year_history():
    runner = CliRunner()
    result = runner.invoke(cli, ["fixtures", "doctor", "--fixtures", str(HISTORY)])
    assert result.exit_code == 0, result.output
    assert "FAIL" not in result.output
    for symbol in NINE_ETFS:
        assert f"{symbol}.parquet" in result.output
        assert "synthetic=False" in result.output


def test_doctor_reports_the_full_ten_year_window():
    runner = CliRunner()
    result = runner.invoke(cli, ["fixtures", "doctor", "--fixtures", str(HISTORY)])
    assert result.exit_code == 0, result.output
    for line in result.output.splitlines():
        if line.strip().startswith("ok"):
            assert "2016-06-01..2026-06-01" in line, line


def test_doctor_accepts_the_macro_series_in_its_own_directory():
    runner = CliRunner()
    result = runner.invoke(cli, ["fixtures", "doctor", "--fixtures", str(MACRO)])
    assert result.exit_code == 0, result.output
    assert "FAIL" not in result.output
    assert "macro.parquet" in result.output
    assert "synthetic=False" in result.output


def test_doctor_refuses_a_synthetic_fixture(tmp_path):
    import json
    import shutil

    shutil.copy(HISTORY / "SPY.parquet", tmp_path / "SPY.parquet")
    meta = json.loads((HISTORY / "SPY.parquet.meta.json").read_text())
    meta["synthetic"] = True
    (tmp_path / "SPY.parquet.meta.json").write_text(json.dumps(meta))

    runner = CliRunner()
    result = runner.invoke(cli, ["fixtures", "doctor", "--fixtures", str(tmp_path)])
    assert result.exit_code != 0
    assert "FAIL" in result.output
