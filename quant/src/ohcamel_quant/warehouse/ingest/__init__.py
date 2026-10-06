"""Ingest job handlers, ``run(params: dict, ctx) -> dict`` (compute plan II.2).

``HANDLERS`` maps job kind -> handler; Lane B's integration step registers it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

HANDLERS: dict[str, Callable[[dict, Any], dict]] = {}
