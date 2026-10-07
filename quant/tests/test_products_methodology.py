"""Lane M: each product has a Methodology entry (quant/web/src/pages/methodology/models.ts, data only)."""

from __future__ import annotations

import re

from ohcamel_quant.config import REPO_ROOT

WEB = REPO_ROOT / "quant" / "web" / "src" / "pages" / "methodology"
# entry id -> section. Each Lane M task appends its entry here.
ENTRIES = {"p1-mc-atlas": "risk"}


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
