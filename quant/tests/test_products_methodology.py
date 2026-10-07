"""Lane M: each product has a Methodology entry (quant/web/src/pages/methodology/models.ts, data only)."""

from __future__ import annotations

import re

from ohcamel_quant.config import REPO_ROOT

WEB = REPO_ROOT / "quant" / "web" / "src" / "pages" / "methodology"
# entry id -> section. Each Lane M task appends its entry here.
ENTRIES = {"p1-mc-atlas": "risk", "p2-vol-league": "options", "p3-cov-league": "portfolio", "p4-strategy-farm": "backtest",
           "p7-surface-history": "options", "p5-exp-q01": "backtest"}


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
