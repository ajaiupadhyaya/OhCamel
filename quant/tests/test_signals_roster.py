"""INC-02's roster as every Quant producer reads it: validated on load, within
its own incubation_cap, and registered in the example book's signals block.
INC-01 closed at 2026-11-05 (its Amendment A1); INC-02, its successor, is a
draft awaiting the owner's approval, and nothing trades until it is approved."""

from __future__ import annotations

import re

import pytest

from ohcamel_quant.config import REPO_ROOT
from ohcamel_quant.models.farm import farm_cells
from ohcamel_quant.signals.roster import ROSTER_PATH, Cell, load_roster

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
