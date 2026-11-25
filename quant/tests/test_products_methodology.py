"""Lane M: each product has a Methodology entry (quant/web/src/pages/methodology/models.ts, data only)."""

from __future__ import annotations

import re

from ohcamel_quant.config import REPO_ROOT

WEB = REPO_ROOT / "quant" / "web" / "src" / "pages" / "methodology"
# entry id -> section. Each Lane M task appends its entry here.
ENTRIES = {"p1-mc-atlas": "risk", "p2-vol-league": "options", "p3-cov-league": "portfolio", "p4-strategy-farm": "backtest",
           "p7-surface-history": "options", "p5-exp-q01": "backtest",
           "p6-exp-q02": "macro", "k01-kronos-shadow": "options", "factor-workbench": "factors"}


def test_each_product_has_an_entry_with_known_references():
    models = (WEB / "models.ts").read_text()
    refs = set(re.findall(r"^  ([a-z0-9_]+): \{", (WEB / "references.ts").read_text(), re.M))
    for eid, section in ENTRIES.items():
        m = re.search(rf'id: "{re.escape(eid)}",\s*name: "[^"]+",\s*section: "(\w+)"', models)
        assert m, f"no Methodology entry {eid}"
        assert m.group(1) == section
        block = models[m.start():models.index("\n  },", m.start())]
        keys = re.findall(r'"([a-z0-9_]+)"', re.search(r"refs: \[([^\]]*)\]", block).group(1))
        assert keys and set(keys) <= refs, f"{eid}: unknown refs {set(keys) - refs}"


def test_atlas_entry_says_what_members_get():
    """Members are FHS only at 97.5% and 99%; the entry must not claim a copula reading for them."""
    from ohcamel_quant.products import atlas

    models = (WEB / "models.ts").read_text()
    start = models.index('id: "p1-mc-atlas"')
    summary = re.search(r'summary: "([^"]+)"', models[start:]).group(1)
    assert atlas.MEMBER_METHODS == ("fhs",) and atlas.MEMBER_ALPHAS == (0.975, 0.99)
    assert "universe member is measured alone by filtered historical simulation only" in summary
    assert "97.5 and 99%" in summary and "no copula is run for members" in summary
    assert "every universe member, by" not in summary


def test_vol_league_entry_states_the_target_the_code_computes():
    from ohcamel_quant.products import vol_league

    models = (WEB / "models.ts").read_text()
    start = models.index('id: "p2-vol-league"')
    block = models[start:models.index("\n  },", start)]
    assert "close-to-close variance of the next session" in block
    assert "the squared overnight return O_t / C_{t-1} − 1" in block
    assert "official open to the first minute bar's close" in block and "last bar's close to the official close" in block
    assert "split-adjusted prices only" in block
    assert f"fewer than {vol_league.MIN_PRINTS} IEX prints" in block
    assert "O_t}{C_{t-1}} - 1" in block          # the formula carries the overnight term
    assert "P_{t,1}}{O_t} - 1" in block and "C_t}{P_{t,n}} - 1" in block   # and the two intraday legs
    vrp_block = models[models.index('id: "p7-surface-history"'):]
    p7 = vrp_block[:vrp_block.index("\n  },")]
    assert "close-to-close variance" in p7 and "OLD TARGET" in p7


def _charter_gates() -> dict[str, str]:
    text = (REPO_ROOT / "docs" / "CHARTER.md").read_text(encoding="utf-8")
    table = text[text.index("## The gates"):text.index("## Data")]
    rows = [line.strip().strip("|").split("|") for line in table.splitlines() if line.startswith("| ")]
    return {test.strip(): rule.strip() for test, rule in rows[1:]}


def test_the_charters_gates_table_is_the_verdicts_constants():
    """Completion task 2.5 (spec ruling R3, awaiting the owner): docs/CHARTER.md's gates table states exactly the lines
    models/verdict.py gates by -- a constant moved without the charter, or the charter without the constant, fails."""
    from ohcamel_quant.models import verdict as V

    rows = _charter_gates()
    assert rows["Walk-forward, out-of-sample"] == "holdout positive"
    assert rows["Deflated Sharpe Ratio (multiple-testing corrected)"] == f"≥ {V.DSR_MIN:.2f}"
    assert rows["Probabilistic Sharpe Ratio"] == f"≥ {V.PSR_MIN:.2f}"
    assert rows["Excess: Hansen's SPA test against the universe's equal-weight buy-and-hold"] == (
        f"p-value ≤ {V.SPA_MAX_P:.2f}")
    assert rows["Probability of Backtest Overfitting"] == "reported; high values named in the verdict"
    assert rows["Stationary-block bootstrap, ~1000 resamples"] == "lower 5th percentile > 0"
    assert rows[f"Regime stress across the {len(V.REGIMES)} regimes of `research/config/regimes.yaml`"] == (
        f"positive in ≥ {V.REGIMES_MIN}; fewer than {V.REGIMES_MIN} with out-of-sample data is INSUFFICIENT DATA, "
        "not FAIL")
    levels = " / ".join(f"{b:g}" for b in V.COST_GRID_BPS)
    assert rows[f"Cost sensitivity at {levels} bps"] == "reported at every level"
    assert len(rows) == 8


def test_the_farm_entry_states_the_charters_gates():
    from ohcamel_quant.models import verdict as V

    models = (WEB / "models.ts").read_text()
    start = models.index('id: "p4-strategy-farm"')
    summary = re.search(r'summary: "([^"]+)"', models[start:]).group(1)
    assert f"deflated Sharpe ≥ {V.DSR_MIN:.2f}" in summary and f"PSR ≥ {V.PSR_MIN:.2f}" in summary
    assert f"SPA p ≤ {V.SPA_MAX_P:.2f} against the universe's equal-weight buy-and-hold" in summary
    assert f"positive in {V.REGIMES_MIN} of {len(V.REGIMES)} regimes" in summary
    assert "fewer than three with out-of-sample data is insufficient data" in summary
    assert "0.30" not in summary and "0.70" not in summary and "three of five" not in summary


def test_the_farm_entry_claims_nothing_about_how_many_rows_fail():
    """Fix round 1 (M-1): the entry is data, not the leaderboard; it says what every row carries, never a count."""
    models = (WEB / "models.ts").read_text()
    start = models.index('id: "p4-strategy-farm"')
    summary = re.search(r'summary: "([^"]+)"', models[start:]).group(1)
    assert "Most rows fail" not in summary
    assert "PASS, FAIL, or INSUFFICIENT DATA" in summary and "whole grid and full sweep window" in summary
