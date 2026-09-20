"""What has been verified, and when -- the research layer's half of
``lib/verified.ml``.

The mirror is deliberate. ``lib/verified.ml`` holds ``tests`` and
``scheduler_tests`` as dated constants rather than computed ones because a
test count is a fact about a moment and a machine, not about the running
process; ``test_ohcamel.ml`` asserts its count against the registry it runs,
so the number cannot drift silently while the prose that quotes it does.
This module exists so the Python suite gets the same guarantee: ``TESTS`` is
asserted against the real collection by ``conftest.py``'s
``pytest_collection_modifyitems`` hook, and ``scripts/check-counts.sh``
greps this file's constant against the three ``<!-- count:research-tests
-->`` markers in ``README.md``, ``docs/overview.md`` and ``docs/status.md``.
Three sentences quoting ``355`` outlived the count going to 388 for exactly
as long as nothing automatic compared them -- this file and that script are
what stops the next drift.
"""

from __future__ import annotations

# research/tests, collected whole (no -k, -m or node-id filter): the number
# conftest.py's pytest_collection_modifyitems hook checks collection against,
# and the number scripts/check-counts.sh greps out of this line.
TESTS = 388
