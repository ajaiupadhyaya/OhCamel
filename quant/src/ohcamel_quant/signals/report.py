"""``desk.report`` (design §4.2; INC-01, *Reporting*): the session as the live
engine recorded it, at 16:30 New York, as an artifact the Research page links.

Read from ``Settings.live_engine_url`` (``OHCAMEL_QUANT_LIVE_ENGINE_URL``,
default ``http://ohcamel-live:8081``, the engine on the internal network), one
GET per route with a :data:`TIMEOUT_S` timeout each:

* ``/api/desk`` -- its ``fills`` (desk/oms.ml ``fills_json``: the newest 20,
  each with its TCA costs in bps) and the effective ``exposure_scale``,
  ``governor`` and ``overlay`` reading (desk/intake.ml ``reading_fields``);
* ``/api/desk/tca`` -- the cost summary, overall and by symbol (it serves no
  per-fill rows: those ride on ``/api/desk``'s fills), into the notes;
* ``/api/research`` -- each registered strategy with its latest judgement
  and the overlay with its document's ``inputs`` and ``reported``;
* ``/api/desk/sessions`` -- the session closes, the day's into the notes.

Tables:

* ``fills`` -- the fills ``/api/desk`` serves whose ``at`` falls on this
  session's New York date, every field the route carries (:data:`FILL_COLUMNS`).
* ``pnl_by_strategy`` -- one row a registered strategy: tier, state, capital
  fraction and its latest judgement. ``pnl`` is null: per-strategy P&L is not
  on the wire until Stage 3 (``/api/research``'s ``record`` is null), and
  nothing here estimates it.
* ``refusals`` -- each strategy's latest judgement whose verdict is
  ``rejected`` or ``advisory`` (rule and detail), and each latest rebalance
  sentence that begins "refused". The route serves only the latest judgement
  a strategy, whatever session received it; ``received_at`` dates each.
* ``overlay_path`` -- one row: the overlay's reading at this run. The intraday
  path is the sequence of ``signals.overlay`` artifacts, which keeps every one.

``verdict`` is None: a report records, it does not judge. An unset URL, an
engine that does not answer (the ``live`` profile not running, say), an error
status or a malformed body is named in the notes and the job SUCCEEDS with
whatever the other routes gave -- empty tables when the engine is down.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from ..jobs.artifacts import ArtifactSpec

__all__ = ["FILL_COLUMNS", "KIND", "ROUTES", "TIMEOUT_S", "run"]

KIND = "desk.report"
TIMEOUT_S = 10.0
NY = ZoneInfo("America/New_York")
ROUTES = ("/api/desk", "/api/desk/tca", "/api/research", "/api/desk/sessions")
# INC-01, *Reporting*: "on every report and page that shows the record".
INC01_SENTENCE = ("incubation records are not evidence of edge; promotion to live sizing is the owner's decision "
                  "after a passing battery, never from this record alone")
REFUSED_VERDICTS = ("rejected", "advisory")

FILL_COLUMNS = ["execution_id", "client_order_id", "symbol", "side", "qty", "price", "at", "decision_price",
                "arrival_bid", "arrival_ask", "shortfall_bps", "delay_bps", "slippage_bps", "half_spread_bps",
                "versus_model_bps", "arrival_after_hours"]
PNL_COLUMNS = ["strategy", "tier", "sizing", "state", "capital_fraction", "verdict", "rule", "sequence", "as_of",
               "received_at", "rebalance", "validation_status", "pnl", "pnl_note"]
REFUSAL_COLUMNS = ["strategy", "source", "verdict", "rule", "detail", "sequence", "as_of", "received_at"]
OVERLAY_COLUMNS = ["read_at", "exposure_scale", "governor", "governor_cap", "value", "as_of", "age_s", "state",
                   "reason", "vol_target", "vol_forecast", "vol_model", "cov_estimator", "inputs", "reported"]
PNL_NOTE = "not on the wire until Stage 3"


def _session_date() -> date:
    return datetime.now(NY).date()


def _now() -> datetime:
    return datetime.now(UTC)


class _Unreachable(Exception):
    """The engine did not answer at all: stop asking it."""


def _get(client: httpx.Client, base: str, path: str, want: type, notes: list[str]) -> Any:
    """The route's parsed body when it is a 200 of type ``want``; else None and a note.
    Raises :class:`_Unreachable` when the request never got an answer."""
    try:
        r = client.get(base + path)
    except httpx.TransportError as e:   # refused, reset, DNS, timeout
        raise _Unreachable(f"GET {path}: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        notes.append(f"GET {path}: HTTP {r.status_code}, not read: {r.text[:200]}")
        return None
    try:
        body = r.json()
    except ValueError as e:
        notes.append(f"GET {path}: malformed, not JSON ({e}); not read")
        return None
    if not isinstance(body, want):
        notes.append(f"GET {path}: malformed, a {type(body).__name__} where the route serves a "
                     f"{want.__name__}; not read")
        return None
    return body


def _ny_date(stamp: Any) -> date | None:
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(NY).date()
    except ValueError:
        return None


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _fills(desk: dict[str, Any], session: date, notes: list[str]) -> list[dict[str, Any]]:
    raw = desk.get("fills")
    if not isinstance(raw, list):
        notes.append("GET /api/desk: no fills list; the fills table is empty")
        return []
    rows, bad = [], 0
    for f in raw:
        if not isinstance(f, dict) or not isinstance(f.get("execution_id"), str) or _ny_date(f.get("at")) is None:
            bad += 1
            continue
        if _ny_date(f["at"]) == session:
            rows.append({c: f.get(c) for c in FILL_COLUMNS})
    if bad:
        notes.append(f"GET /api/desk: {bad} fill rows skipped (not a fill with an execution_id and a readable 'at')")
    notes.append(f"fills: {len(rows)} of the {len(raw)} fills /api/desk serves are this session's ({session}); "
                 "the route serves the newest 20, so a session with more is cut there")
    return rows


def _strategies(research: dict[str, Any], notes: list[str]) -> tuple[list[dict], list[dict]]:
    raw = research.get("strategies")
    if not isinstance(raw, list):
        notes.append("GET /api/research: malformed, no strategies list; not read")
        return [], []
    pnl, refusals = [], []
    for s in raw:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str):
            notes.append(f"GET /api/research: a strategy row without a name skipped: {str(s)[:100]}")
            continue
        latest = s.get("latest") if isinstance(s.get("latest"), dict) else {}
        validation = latest.get("validation") if isinstance(latest.get("validation"), dict) else {}
        pnl.append({"strategy": s["name"], "tier": s.get("tier"), "sizing": s.get("sizing"), "state": s.get("state"),
                    "capital_fraction": _num(s.get("capital_fraction")), "verdict": latest.get("verdict"),
                    "rule": latest.get("rule"), "sequence": latest.get("sequence"), "as_of": latest.get("as_of"),
                    "received_at": latest.get("received_at"), "rebalance": latest.get("rebalance"),
                    "validation_status": validation.get("status"), "pnl": None, "pnl_note": PNL_NOTE})
        common = {"strategy": s["name"], "sequence": latest.get("sequence"), "as_of": latest.get("as_of"),
                  "received_at": latest.get("received_at")}
        if latest.get("verdict") in REFUSED_VERDICTS:
            refusals.append({**common, "source": "judgement", "verdict": latest["verdict"],
                             "rule": latest.get("rule"), "detail": latest.get("detail")})
        rebalance = latest.get("rebalance")
        if isinstance(rebalance, str) and rebalance.startswith("refused"):
            refusals.append({**common, "source": "rebalance", "verdict": latest.get("verdict"), "rule": None,
                             "detail": rebalance})
    notes.append("pnl_by_strategy: per-strategy P&L is not on the wire until Stage 3 (/api/research's record is "
                 "null); the pnl column is null, never estimated")
    notes.append("refusals: /api/research serves each strategy's latest judgement only, whatever session "
                 "received it; received_at dates each")
    return pnl, refusals


def _overlay(desk: dict[str, Any] | None, research: dict[str, Any] | None, read_at: str) -> list[dict[str, Any]]:
    d_ov = desk.get("overlay") if desk and isinstance(desk.get("overlay"), dict) else {}
    r_ov = research.get("overlay") if research and isinstance(research.get("overlay"), dict) else {}
    if not d_ov and not r_ov and not (desk and "exposure_scale" in desk):
        return []
    ov = {**d_ov, **r_ov}
    inputs = ov.get("inputs") if isinstance(ov.get("inputs"), dict) else None
    reported = ov.get("reported") if isinstance(ov.get("reported"), dict) else None
    i = inputs or {}
    return [{"read_at": read_at, "exposure_scale": _num(desk.get("exposure_scale")) if desk else None,
             "governor": _num(ov.get("governor", desk.get("governor") if desk else None)),
             "governor_cap": _num(ov.get("governor_cap")), "value": _num(ov.get("value")),
             "as_of": ov.get("as_of"), "age_s": ov.get("age_s"), "state": ov.get("state"),
             "reason": ov.get("reason"), "vol_target": _num(i.get("vol_target")),
             "vol_forecast": _num(i.get("vol_forecast")), "vol_model": i.get("vol_model"),
             "cov_estimator": i.get("cov_estimator"),
             "inputs": None if inputs is None else json.dumps(inputs, sort_keys=True),
             "reported": None if reported is None else json.dumps(reported, sort_keys=True)}]


def _tca_notes(tca: dict[str, Any], notes: list[str]) -> None:
    overall = tca.get("overall") if isinstance(tca.get("overall"), dict) else {}
    notes.append(f"tca (the newest 500 fills, not only this session's): {tca.get('note')}; overall "
                 f"count {overall.get('count')}, mean shortfall {overall.get('mean_shortfall_bps')} bps, "
                 f"median {overall.get('median_shortfall_bps')} bps")


def _session_notes(sessions: list[Any], session: date, notes: list[str]) -> None:
    rows = [s for s in sessions if isinstance(s, dict) and s.get("date") == session.isoformat()]
    if rows:
        s = rows[-1]
        notes.append(f"session close {session}: equity_close {s.get('equity_close')}, cash_close "
                     f"{s.get('cash_close')}, gross_close {s.get('gross_close')}, net_close {s.get('net_close')}")
    else:
        notes.append(f"no session close recorded for {session} yet in /api/desk/sessions")


def _spec(session: date, fills: list[dict], pnl: list[dict], refusals: list[dict], overlay: list[dict],
          provenance: list[dict], notes: list[str]) -> ArtifactSpec:
    notes.append(INC01_SENTENCE)
    return ArtifactSpec(
        tables={"fills": pd.DataFrame(fills, columns=FILL_COLUMNS),
                "pnl_by_strategy": pd.DataFrame(pnl, columns=PNL_COLUMNS),
                "refusals": pd.DataFrame(refusals, columns=REFUSAL_COLUMNS),
                "overlay_path": pd.DataFrame(overlay, columns=OVERLAY_COLUMNS)},
        data_asof=session.isoformat(), provenance=provenance, notes=notes, verdict=None)


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    session = _session_date()
    read_at = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    notes: list[str] = []
    base = (ctx.market.settings.live_engine_url or "").strip().rstrip("/")
    if not base:
        notes.append("OHCAMEL_QUANT_LIVE_ENGINE_URL is unset: no engine to read, the report is empty")
        return _spec(session, [], [], [], [], [{"source": "ohcamel-live", "url": None}], notes)
    bodies: dict[str, Any] = {}
    want = {"/api/desk": dict, "/api/desk/tca": dict, "/api/research": dict, "/api/desk/sessions": list}
    try:
        with httpx.Client(timeout=TIMEOUT_S) as client:
            for path in ROUTES:
                bodies[path] = _get(client, base, path, want[path], notes)
    except _Unreachable as e:
        notes.append(f"the live engine at {base} did not answer ({e}): the live profile is not running, or the "
                     "engine is down; the report is empty")
        return _spec(session, [], [], [], [], [{"source": "ohcamel-live", "url": base, "answered": False}], notes)
    desk, tca, research, sessions = (bodies[p] for p in ROUTES)
    fills = _fills(desk, session, notes) if desk is not None else []
    pnl, refusals = _strategies(research, notes) if research is not None else ([], [])
    overlay = _overlay(desk, research, read_at)
    if tca is not None:
        _tca_notes(tca, notes)
    if sessions is not None:
        _session_notes(sessions, session, notes)
    if desk is not None:
        notes.append(f"account session_pnl {desk.get('session_pnl')} (the whole paper account, not per strategy)")
    provenance = [{"source": "ohcamel-live", "url": base, "route": p, "read": bodies[p] is not None} for p in ROUTES]
    return _spec(session, fills, pnl, refusals, overlay, provenance, notes)
