"""INC-02's roster as every Quant producer reads it: validated on load, within
its own incubation_cap, and registered in the example book's signals block.
INC-01 closed at 2026-11-05 (its Amendment A1); INC-02, its successor, is a
draft awaiting the owner's approval, and nothing trades until it is approved."""

from __future__ import annotations

import re

import pytest

from ohcamel_quant.config import REPO_ROOT
from ohcamel_quant.models.farm import farm_cells
from ohcamel_quant.signals.roster import (
    ROSTER_PATH,
    Cell,
    load_roster,
    recorded_approval,
    roster_draft,
)

BOOK = REPO_ROOT / "book.example.sexp"


def test_load_roster_reads_inc02_within_its_incubation_cap():
    assert ROSTER_PATH.parent.name == "INC-02"
    roster = load_roster()
    assert len(roster.cells) == 22
    total = sum(c.capital_fraction for c in roster.cells)
    # 22 x 0.025 = 0.55 = incubation_cap: pairs_coint x sectors and EXP-Q01's held slot are gone.
    assert total == pytest.approx(0.55, abs=1e-9)
    assert total <= roster.incubation_cap + 1e-9
    assert roster.incubation_cap == 0.55
    assert roster.vol_target == 0.10
    spy = next(c for c in roster.cells if c.slug == "exp_a01_spy")
    assert spy == Cell("exp_a01_spy", "research-service", None, None, (), 0.025)
    farm = next(c for c in roster.cells if c.slug == "farm_tsmom_us_equity_indices")
    assert farm.producer == "signals.daily"
    assert farm.strategy == "tsmom" and farm.universe == "us_equity_indices"
    assert farm.symbols == ("SPY", "QQQ", "DIA", "IWM", "MDY")


def test_the_rosters_farm_rows_are_the_farms_cells():
    """INC-02's roster is the farm's cells that can run (spec, Research, *Roster*), in the farm's order."""
    rows = [(c.strategy, c.universe) for c in load_roster().cells if c.producer == "signals.daily"]
    assert rows == farm_cells()


def test_the_roster_is_a_draft_until_its_preregistration_records_the_owners_approval(tmp_path):
    """The label keys off the pre-registration's own ``Approved-by:`` line, so it goes by itself on approval."""
    assert roster_draft() == "INC-02"
    exp = tmp_path / "INC-09"
    exp.mkdir()
    roster = exp / "roster.yaml"
    assert roster_draft(roster) == "INC-09"                                  # no pre-registration: no approval
    (exp / "preregistration.md").write_text("# INC-09\n\n**Status: DRAFT.**\n\n## Approval\n\nApproved-by:\n")
    assert roster_draft(roster) == "INC-09"                                  # an empty Approved-by: approves nothing
    (exp / "preregistration.md").write_text("# INC-09\n\n## Approval\n\nApproved-by: owner, 2026-12-01\n")
    assert roster_draft(roster) is None
    assert recorded_approval("**Status: APPROVED 2026-10-11 as written.**") == "2026-10-11"


def test_roster_slugs_are_registered_in_book_example_sexp():
    """Only the signals block: the limits block has ``(name …)`` rows too."""
    text = "\n".join(ln.split(";", 1)[0] for ln in BOOK.read_text().splitlines())
    signals = text[text.index("(signals") :]
    names = set(re.findall(r"\(name ([a-z][a-z0-9_]+)\)", signals))
    assert {c.slug for c in load_roster().cells} <= names


_HEAD = "version: 1\nincubation_cap: 0.60\ncanary_cap: 0.10\nvol_target: 0.10\nstrategies:\n"
_ROW = "  - slug: {slug}\n    producer: signals.daily\n    capital_fraction: {f}\n"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (_ROW.format(slug="a_one", f=0.1) + "    colour: red\n", "unknown"),
        (_ROW.format(slug="a_one", f=0), "capital_fraction"),
        (_ROW.format(slug="a_one", f=1.5), "capital_fraction"),
        (_ROW.format(slug="Bad-Slug", f=0.1), "slug"),
        (_ROW.format(slug="a_one", f=0.1) + _ROW.format(slug="a_one", f=0.1), "duplicate"),
        (_ROW.format(slug="a_one", f=0.4) + _ROW.format(slug="a_two", f=0.4), "incubation_cap"),
        (_ROW.format(slug="a_one", f=0.1).replace("signals.daily", "elsewhere"), "producer"),
    ],
)
def test_load_roster_refuses_a_malformed_roster(tmp_path, body, message):
    path = tmp_path / "roster.yaml"
    path.write_text(_HEAD + body)
    with pytest.raises(ValueError, match=message):
        load_roster(path)


def test_load_roster_refuses_an_unknown_top_level_key(tmp_path):
    path = tmp_path / "roster.yaml"
    path.write_text("extra: 1\n" + _HEAD + _ROW.format(slug="a_one", f=0.1))
    with pytest.raises(ValueError, match="unknown"):
        load_roster(path)


# ---- completion task 2.2: the stop's volatility, written into the book ----------------------------------------------
def test_the_example_book_gives_every_roster_row_an_ex_ante_vol_and_says_which_are_measured():
    """book.example.sexp is held to the roster row for row; here, each row's ``(ex_ante_vol F)`` and the stop words:
    EXP-A01's two are the committed script output's, the farm's 20 are the stated placeholder (0.075)."""
    import json
    import re

    from ohcamel_quant.config import REPO_ROOT

    text = (REPO_ROOT / "book.example.sexp").read_text()
    text = text[text.index("\n (signals\n"):]
    blocks = re.split(r"(?=\(\(name\s)", text)[1:]
    vols = {}
    for b in blocks:
        slug = re.match(r"\(\(name (\w+)\)", b).group(1)
        assert "(stop ((drawdown" not in b, f"{slug} still carries INC-01's stop"
        for word in ("(stop_vol_multiple 2.0)", "(stop_floor 0.05)", "(stop_cap 0.25)", "(stop_sessions 500)"):
            assert word in b, f"{slug} lacks {word}"
        vols[slug] = float(re.search(r"\(ex_ante_vol ([^)\s]+)\)", b).group(1))
    assert list(vols) == [c.slug for c in load_roster().cells]
    measured = json.loads((REPO_ROOT / "research/experiments/INC-02/exp_a01_vol.json").read_text())
    for slug, row in measured.items():
        assert vols[slug] == pytest.approx(row["ex_ante_vol"], abs=5e-7), slug
    assert all(v == 0.075 for s, v in vols.items() if s.startswith("farm_")), "a farm row that is neither measured nor the placeholder"
    for b in blocks:
        slug = re.match(r"\(\(name (\w+)\)", b).group(1)
        assert ("(ex_ante_vol_source placeholder)" in b) == slug.startswith("farm_"), slug


def test_book_ex_ante_vol_rewrites_farm_rows_from_the_freeze_and_refuses_a_missing_one():
    import importlib.util

    from ohcamel_quant.config import REPO_ROOT

    spec = importlib.util.spec_from_file_location("book_ex_ante_vol", REPO_ROOT / "tools" / "book_ex_ante_vol.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    book = ("((name exp_a01_spy) (ex_ante_vol 0.0957))\n"
            "((name farm_a) (ex_ante_vol 0.075) (ex_ante_vol_source placeholder))\n"
            "((name farm_b) (ex_ante_vol 0.075) (ex_ante_vol_source placeholder))")
    new, done, refused = tool.rewrite(book, {"farm_a": 0.0421, "exp_a01_spy": 9.0})
    assert done == ["farm_a"] and refused == ["farm_b"]
    assert "(ex_ante_vol 0.042100)" in new and "(ex_ante_vol 0.0957)" in new   # EXP-A01 untouched
    assert new.count("(ex_ante_vol 0.075)") == 1   # the refused row keeps what it held ...
    assert new.count("(ex_ante_vol_source placeholder)") == 1   # ... and its placeholder mark; farm_a's is gone
