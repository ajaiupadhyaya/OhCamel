#!/usr/bin/env python3
"""The Python halves of scripts/check-done.sh (completion Task 5.3): one subcommand per check that needs the code.

Each prints exactly one line -- ``ok``, ``FAIL: <why>`` or ``not checked: <why>`` -- and exits 0; the shell decides
the exit status from the line. READ-ONLY: a journal is opened ``mode=ro&immutable=1``, artifacts and ledgers are
read, nothing is written (the American-chain inversion and the SVI fits run in memory).

    check_done_probe.py ledger  JOURNAL            check 2: the sub-ledger sums, in shares and in dollars
    check_done_probe.py farm    ARTIFACT_DIR [LEDGER]   check 3: the farm's traded configuration and trial count
    check_done_probe.py inc02                       check 4: INC-02's approval line and the pooled_test product
    check_done_probe.py chain                       check 6: the committed SPY chain, the put/call IVs, the target

Run with the project's own environment (quant/.venv/bin/python), from anywhere.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant" / "src"))
sys.path.insert(0, str(ROOT / "quant" / "tests"))

#: The tolerance desk/subledger.ml states (``let tolerance = 0.5``): read from the OCaml, not retyped here.
SUBLEDGER_ML = ROOT / "desk" / "subledger.ml"
#: Check 2's dollar bound: the plan's "within $1".
PNL_TOLERANCE_USD = 1.0
#: Check 6's bounds (Task 4.1's amended density condition; Task 4.2's same-strike condition).
G_FLOOR = -1e-10
PUT_CALL_POINTS = 0.5
RESOLVED_HALF_WIDTH = 0.25   # a leg's IV is pinned by its own bid/ask within this many vol points either side


def _out(line: str) -> int:
    print(line)
    return 0


def subledger_tolerance() -> float:
    m = re.search(r"^let tolerance\s*=\s*([0-9.]+)", SUBLEDGER_ML.read_text(encoding="utf-8"), re.M)
    if not m:
        raise RuntimeError(f"{SUBLEDGER_ML}: no `let tolerance = <float>` line")
    return float(m.group(1))


def _owner(source: str | None) -> str:
    """Subledger.owner: ``signal:<slug>:<sequence>`` is the strategy <slug>'s; anything else is the hand's."""
    parts = (source or "").split(":")
    return ":".join(parts[1:-1]) if parts[0] == "signal" and len(parts) >= 3 else "<hand>"


def ledger(path: str) -> int:
    """Subledger.check as desk/oms.ml feeds it, from the journal alone: per name, every owner's net fills summed
    against the account's position at the last session close (the marks table, which the close records from the
    venue); a name beyond the tolerance, or not a finite number, is a break. Then the same sum in dollars:
    every owner's marked P&L (fills at their prices, the net position at the last close) summed, against the
    account's marked P&L over the same fills at the account's position -- they differ by break x close, so the
    dollar figure is the break's value. Fees, dividends and interest are not in this figure (they name no owner)."""
    p = Path(path)
    if not p.is_file():
        return _out(f"not checked: no journal at {path}")
    tol = subledger_tolerance()
    con = sqlite3.connect(f"file:{p.resolve()}?mode=ro&immutable=1", uri=True)
    try:
        last = con.execute("SELECT max(date) FROM sessions").fetchone()[0]
        if last is None:
            return _out("not checked: the journal holds no session close")
        marks = {s: (c, q) for s, c, q in con.execute("SELECT symbol, close, qty FROM marks WHERE date = ?", (last,))}
        rows = con.execute("SELECT o.source, f.symbol, f.side, f.qty, f.price FROM fills f "
                           "LEFT JOIN orders o ON o.client_order_id = f.client_order_id").fetchall()
    finally:
        con.close()
    if not rows:
        return _out("not checked: the journal holds no fills")
    own: dict[tuple[str, str], float] = {}
    cash: dict[tuple[str, str], float] = {}
    last_price: dict[str, float] = {}
    for source, sym, side, qty, price in rows:
        sign = 1.0 if str(side).lower().startswith("b") else -1.0
        k = (_owner(source), sym)
        own[k] = own.get(k, 0.0) + sign * qty
        cash[k] = cash.get(k, 0.0) - sign * qty * price
        last_price[sym] = price
    names = sorted({s for _, s in own} | {s for s, (_, q) in marks.items() if q})
    breaks, strat_pnl, acct_pnl = [], 0.0, 0.0
    for sym in names:
        mine = sum(q for (_, s), q in own.items() if s == sym)
        acct = marks.get(sym, (None, 0.0))[1]
        close = marks[sym][0] if sym in marks else last_price.get(sym, float("nan"))
        if not (abs(mine - acct) <= tol):                  # also true of NaN: an unknown is never a sum that holds
            breaks.append(f"{sym} strategies {mine:g} vs account {acct:g}")
        strat_pnl += sum(cash[k] + own[k] * close for k in own if k[1] == sym)
        acct_pnl += sum(c for k, c in cash.items() if k[1] == sym) + acct * close
    if breaks:
        return _out(f"FAIL: sub-ledger break at the close of {last}: " + "; ".join(breaks))
    gap = abs(strat_pnl - acct_pnl)
    if not gap <= PNL_TOLERANCE_USD:
        return _out(f"FAIL: strategy P&L {strat_pnl:.2f} against the account's {acct_pnl:.2f} differ by ${gap:.2f}")
    return _out("ok")


def farm(artifact_dir: str, ledger_path: str | None) -> int:
    """The farm artifact's manifest and tables: every scored cell at fill ``open`` and max_gross 1 (the desk's
    traded configuration), a ``capacity_usd`` column, and one trial count in three places -- the manifest's
    trial-ledger provenance, the artifact's ``ledger`` table, and the ledger file's distinct trials."""
    import pandas as pd

    from ohcamel_quant.models import trial_ledger
    from ohcamel_quant.products import farm as F

    d = Path(artifact_dir)
    if not (d / "manifest.json").is_file():
        return _out(f"not checked: no farm artifact at {artifact_dir}")
    m = json.loads((d / "manifest.json").read_text())
    if m.get("kind") not in (None, "farm.sweep"):
        return _out(f"FAIL: {artifact_dir} is a {m.get('kind')!r} artifact, not farm.sweep")
    led_file = Path(ledger_path) if ledger_path else d.parent.parent / trial_ledger.LEDGER_NAME
    if not led_file.is_file():
        return _out(f"not checked: no trial ledger at {led_file}")
    cells = pd.read_parquet(d / "cells.parquet")
    why = []
    scored = cells[cells["status"] == "ok"] if "status" in cells.columns else cells
    if scored.empty:
        why.append("no scored cell")
    if "fill" not in cells.columns or not (scored["fill"] == F.FILL).all():
        why.append(f"a scored cell's fill is not {F.FILL!r}")
    if "max_gross" not in cells.columns or not (scored["max_gross"].astype(float) == F.MAX_GROSS).all():
        why.append(f"a scored cell's max_gross is not {F.MAX_GROSS:g}")
    if "capacity_usd" not in cells.columns:
        why.append("the cells table has no capacity_usd column")
    prov = [p for p in m.get("provenance", []) if p.get("source") == "trial-ledger"]
    ledger_rows = int(pd.read_parquet(d / "ledger.parquet").shape[0]) if (d / "ledger.parquet").is_file() else None
    distinct = int(len(trial_ledger.distinct(led_file)))
    counted = prov[0].get("distinct_trials") if prov else None
    if counted is None or ledger_rows is None:
        why.append("the manifest or the artifact carries no trial count")
    elif not (counted == ledger_rows == distinct):
        why.append(f"trial count: manifest {counted}, ledger table {ledger_rows}, ledger file {distinct}")
    return _out("ok" if not why else "FAIL: " + "; ".join(why))


def inc02() -> int:
    """INC-02's pre-registration records an approval (the ``Approved-by:`` line names a date, the reading the
    ledger and the pooled test use: signals.roster.recorded_approval) and the ``pooled_test`` product exists
    (desk.report's table and the pooled test's constants)."""
    from ohcamel_quant.signals import pooled, report, roster

    text = roster.preregistration_text()
    line = next((x.strip() for x in text.splitlines() if x.strip().startswith(("Approved-by:", "`Approved-by:"))), None)
    why = []
    if line is None:
        why.append("INC-02's pre-registration has no Approved-by line")
    elif not roster.recorded_approval(text):
        why.append("INC-02 is not approved: its Approved-by line is empty (the approval is the owner's to give)"
                   if not line.split(":", 1)[1].strip(" `") else
                   f"INC-02's Approved-by line carries no ISO date: {line!r}")
    if not getattr(report, "POOLED_COLUMNS", None):
        why.append("pooled_test is not a table of desk.report")
    if pooled.SESSIONS <= 0:
        why.append("the pooled test has no horizon")
    return _out("ok" if not why else "FAIL: " + "; ".join(why))


def chain() -> int:
    """Check 6, on the committed 2026-11-15 SPY chain (quant/tests/data, tests/conftest.py reads it):

    * every slice's SVI density passes Task 4.1's amended condition -- no DensityError, negative mass within
      tolerance, the finite-difference density's grid integral within 1e-3 of the closed form's, the mean within
      0.5% of the forward -- and g >= -1e-10 on a 600,001-point grid over [-K_MAX, K_MAX];
    * same-strike put and call American IVs, each leg pinned within +-0.25 points by its own bid/ask, differ by
      under 0.5 points (Task 4.2);
    * the vol league's target note names the quantity its code scores: ``TARGET_LABEL`` is the first words of
      ``_daily_rv``'s docstring and is what ``run`` writes into the note."""
    import inspect

    import numpy as np
    from conftest import _spy_2026_11_15_chain

    from ohcamel_quant.options import svi
    from ohcamel_quant.options.chain import analyze_chain
    from ohcamel_quant.options.density import DensityError, breeden_litzenberger, density_checks
    from ohcamel_quant.options.surface import build_surface
    from ohcamel_quant.products import vol_league

    why: list[str] = []
    ch, curve = _spy_2026_11_15_chain(as_the_review_read_it=True)
    ca = analyze_chain(ch, rate_curve=curve, exercise="european")
    vs = build_surface(ca)
    fine = np.linspace(-svi.K_MAX, svi.K_MAX, 600_001)
    for sid, f in vs.fits.items():
        s = ca.slices.loc[sid]
        F_, D_, T_ = float(s["forward"]), float(s["discount"]), float(s["T"])
        try:
            d = breeden_litzenberger(F_, D_, T_, f.params.w)
        except DensityError as e:
            why.append(f"{sid}: DensityError ({e})")
            continue
        chk = density_checks(d, svi.density_k(f.params, np.log(d.strikes / F_)) / d.strikes)
        failed = sorted(n for n, ok in chk["passed"].items() if not ok)
        if failed:
            why.append(f"{sid}: density fails {','.join(failed)}")
        g = float(svi.butterfly_g(f.params, fine).min())
        if not g >= G_FLOOR:
            why.append(f"{sid}: g {g:.2e} < {G_FLOOR:g}")
    if not vs.fits:
        why.append("no SPY slice fitted")

    ch, curve = _spy_2026_11_15_chain()
    am = analyze_chain(ch, rate_curve=curve)
    q = am.quotes
    cols = ["slice", "strike", "k", "iv", "iv_bid", "iv_ask", "valid"]
    j = q[q["type"] == "C"][cols].merge(q[q["type"] == "P"][cols], on=["slice", "strike"], suffixes=("_c", "_p"))
    j = j[j["valid_c"] & j["valid_p"] & np.isfinite(j["iv_c"]) & np.isfinite(j["iv_p"]) & (j["k_c"].abs() < 0.15)]
    j = j[((j["iv_ask_c"] - j["iv_bid_c"]) * 50 <= RESOLVED_HALF_WIDTH)
          & ((j["iv_ask_p"] - j["iv_bid_p"]) * 50 <= RESOLVED_HALF_WIDTH)]
    gap = ((j["iv_p"] - j["iv_c"]) * 100).abs()
    if j.empty:
        why.append("no same-strike put/call pair is pinned by its own quotes")
    elif not gap.max() < PUT_CALL_POINTS:
        w = j.loc[gap.idxmax()]
        why.append(f"put/call IV differ by {gap.max():.2f} points at {w['slice']} {w['strike']:g}")

    label = vol_league.TARGET_LABEL
    if not (inspect.getdoc(vol_league._daily_rv) or "").lower().startswith(f"the league's one scoring target: {label}"):
        why.append("the league's target docstring does not name " + repr(label))
    if "{TARGET_LABEL}" not in inspect.getsource(vol_league.run):
        why.append("the league's note does not write TARGET_LABEL")
    return _out("ok" if not why else "FAIL: " + "; ".join(why))


def main(argv: list[str]) -> int:
    usage = "usage: check_done_probe.py ledger JOURNAL | farm DIR [LEDGER] | inc02 | chain"
    try:
        if len(argv) >= 2 and argv[0] == "ledger":
            return ledger(argv[1])
        if len(argv) >= 2 and argv[0] == "farm":
            return farm(argv[1], argv[2] if len(argv) > 2 else None)
        if argv[:1] == ["inc02"]:
            return inc02()
        if argv[:1] == ["chain"]:
            return chain()
    except Exception as e:  # noqa: BLE001 -- a check that cannot run never passes by raising
        # the two that read data the caller gives say "not checked"; the two that read the code say FAIL
        return _out(f"{'not checked' if argv[0] in ('ledger', 'farm') else 'FAIL'}: {type(e).__name__}: {e}")
    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
