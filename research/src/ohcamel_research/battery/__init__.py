"""The charter's validation battery: the gates fdq's runner does not compute
(PSR, a stationary-block bootstrap, regime stress, a cost sweep), plus the
turnover and capacity numbers the charter requires every report to carry, and
the runner (``run.py``) with every loader it reads (``data.py``) and the
experiment config it validates (``config.py``).

Nothing under ``battery/`` imports ``ohcamel_research.*`` from outside it,
except ``ohcamel_research.manifest`` (the judge), so every line a verdict
depends on is hashed into the manifest that records it
(``tests/test_battery_run.py`` holds the boundary)."""

from __future__ import annotations

# The date of docs/CHARTER.md whose gates apply. Every manifest records it
# (``Manifest.gates_version``) and every signal's validation block carries it
# (``contract.py`` imports it from here), so there is one copy, and it lives
# where the manifest's hash can see it. This module imports nothing heavy, so
# reading the constant costs the signal path nothing.
GATES_VERSION = "2026-09-02"
