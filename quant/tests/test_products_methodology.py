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
