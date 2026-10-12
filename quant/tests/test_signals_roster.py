"""INC-01's roster as every Quant producer reads it: validated on load, within
its own incubation_cap, and registered in the example book's signals block."""

from __future__ import annotations

import re

import pytest

from ohcamel_quant.config import REPO_ROOT
from ohcamel_quant.signals.roster import Cell, load_roster

BOOK = REPO_ROOT / "book.example.sexp"


def test_load_roster_reads_inc01_within_its_incubation_cap():
    roster = load_roster()
    assert len(roster.cells) == 23
    total = sum(c.capital_fraction for c in roster.cells)
    # 23 x 0.025 = 0.575; the other 0.025 of incubation_cap 0.60 is held for EXP-Q01.
    assert total == pytest.approx(0.575, abs=1e-9)
    assert total <= roster.incubation_cap + 1e-9
    assert roster.incubation_cap == 0.60
    assert roster.vol_target == 0.10
    spy = next(c for c in roster.cells if c.slug == "exp_a01_spy")
    assert spy == Cell("exp_a01_spy", "research-service", None, None, (), 0.025)
    farm = next(c for c in roster.cells if c.slug == "farm_tsmom_us_equity_indices")
    assert farm.producer == "signals.daily"
    assert farm.strategy == "tsmom" and farm.universe == "us_equity_indices"
    assert farm.symbols == ("SPY", "QQQ", "DIA", "IWM", "MDY")


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
