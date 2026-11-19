"""Ingest job handlers, ``run(params: dict, ctx) -> dict`` (compute plan II.2).

``HANDLERS`` maps job kind -> handler; Lane B's integration step registers it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

HANDLERS: dict[str, Callable[[dict, Any], dict]] = {}

from .universes import run_universes  # noqa: E402

HANDLERS["ingest.universes"] = run_universes

from . import bars, calendar  # noqa: E402

# completion task 3.6: the exchange calendar is refreshed inside the nightly bars run (ingest/calendar.py)
HANDLERS["ingest.bars_daily"] = calendar.with_calendar(bars.run_bars_daily)

from . import minute  # noqa: E402

HANDLERS["ingest.bars_minute"] = minute.run_bars_minute

from . import filings, macro  # noqa: E402

HANDLERS["ingest.fred_warehouse"] = macro.run_fred
HANDLERS["ingest.factors"] = macro.run_factors
HANDLERS["ingest.sec_facts"] = filings.run_sec_facts
HANDLERS["ingest.holdings_13f"] = filings.run_holdings_13f

from . import options  # noqa: E402

HANDLERS["ingest.option_snapshots"] = options.run_option_snapshots

from . import corp_actions  # noqa: E402

HANDLERS["ingest.corp_actions"] = corp_actions.run_corp_actions
