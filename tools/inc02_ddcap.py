#!/usr/bin/env python3
"""What a drawdown cap of 0.02, 0.05 or 0.10 would have done to the incubation book, on the committed fixtures.

INC-02 argues its drawdown cap (`dd-cap`) on its merits before any INC-02 session trades (completion spec,
ruling R1). This script is the argument's arithmetic, on real committed data only: the nine Alpaca ETFs of
``fixtures/history/`` (2016-06-01 .. 2026-06-01, provenance in its README) read offline through the Quant
package's own fixture path. Nothing is fetched and nothing is generated.

The book (a proxy, said as one -- the fixtures hold 9 of the roster's 42 ETFs):

* every row of ``research/experiments/INC-02/roster.yaml``; a farm row runs its strategy at its DEFAULT
  parameters (no selection, so no in-sample choice flatters the curve) on its universe cut to the fixture
  ETFs it holds; EXP-A01's two rows run ``sma_trend`` (Faber's 10-month average, the rule EXP-A01 tests)
  on SPY and on TLT; a row whose cut universe is empty or below the strategy's ``min_assets`` holds cash;
* each strategy at the traded configuration (completion task 2.4): gross 1, decided at the close, filled
  at the next open, the cost file ``research/config/costs.yaml``'s half-spreads and borrow;
* pooled at 0.025 of equity each, rebalanced to those fractions daily (the book's return is the sum of
  0.025 x each strategy's net return); a second column rescales the rows that run to the book's full
  0.55 (each 0.55 / n), the conservative reading for a drawdown;
* the overlay is not simulated (the covariance league's artifacts are not fixtures); the book's own
  trailing volatility is printed so a reader can see whether a 0.10 vol target would have bound.

The governor (INC-01 *The overlay*; the desk's rule): the drawdown is the equity's fall from the peak of the
last 250 closes; the governor is 1 up to cap / 2 and falls linearly to 0 at the cap; the next session's
exposure is the book's times the governor (a daily approximation of the desk's intraday re-sizing).
A close at or past the cap trips the kill switch; under INC-02's reduce-through-trip the governor's cut to
zero goes out, so the book is flat from the trip until the owner resets it, which this run never does.

    cd quant && uv run --frozen python ../tools/inc02_ddcap.py [--out ../research/experiments/INC-02/ddcap_fixture.txt]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROSTER = ROOT / "research" / "experiments" / "INC-02" / "roster.yaml"
CAPS = (0.02, 0.05, 0.10)
WINDOW = 250                 # book.example.sexp (risk ((drawdown_window 250)))
VOL_TARGET = 0.10
BOOK_GROSS = 0.55
FRACTION = 0.025


def _setup() -> None:
    os.environ["OHCAMEL_QUANT_OFFLINE"] = "1"
    os.environ.setdefault("OHCAMEL_QUANT_DATA_DIR", tempfile.mkdtemp(prefix="inc02-ddcap-"))


def strategy_returns():
    """slug -> (net daily returns or None for cash, the tickers it ran on, a note)."""
    import yaml

    from ohcamel_quant.backtest import costs as cost_file
    from ohcamel_quant.backtest.engine import EngineConfig, StrategyContext, run_backtest
    from ohcamel_quant.backtest.strategies import get_strategy
    from ohcamel_quant.config import get_settings
    from ohcamel_quant.data import fixtures
    from ohcamel_quant.data.market import get_market
    from ohcamel_quant.jobs.context import JobContext
    from ohcamel_quant.products import farm

    get_settings.cache_clear()
    get_market.cache_clear()
    ctx = JobContext(job_id="inc02-ddcap", params={}, threads=1, progress_fn=lambda f, m: None,
                     cancelled_fn=lambda: False, _market=get_market())
    have = set(fixtures.fixture_symbols())
    model = cost_file.load()
    rows = yaml.safe_load(ROSTER.read_text())["strategies"]
    out = {}
    for row in rows:
        if row["producer"] == "research-service":
            key, tickers = "sma_trend", [row["slug"].rsplit("_", 1)[1].upper()]
            note = "EXP-A01 proxy: sma_trend(months=10)"
        else:
            key, tickers = row["strategy"], [t for t in farm._universe(row["universe"]) if t in have]
            note = f"{key} on {row['universe']} cut to the fixtures"
        spec = get_strategy(key)
        if len(tickers) < spec.min_assets:
            out[row["slug"]] = (None, tickers, f"cash: {len(tickers)} fixture ETF(s) of {row.get('universe', '')}, "
                                                 f"{key} needs {spec.min_assets}")
            continue
        px, opens = farm._prices_and_opens(ctx, tickers)
        mkt = ctx.market.prices(["SPY"], warehouse_first=True).data["SPY"].reindex(px.index) if spec.needs_market \
            else None
        sctx = StrategyContext(prices=px, rf=None, market=mkt, opens=opens)
        cfg = EngineConfig(rebalance=spec.default_rebalance, max_gross_leverage=farm.MAX_GROSS, costs=model,
                           fill=farm.FILL, execution_lag=farm.EXECUTION_LAG)
        params = {p.name: p.default for p in spec.params}
        res = run_backtest(sctx, spec.fn, params, cfg)
        live = res.live_start.date() if res.live_start is not None else "-"
        out[row["slug"]] = (res.returns_net, tickers, f"{note}; live from {live}")
    return out, model.version


def governed(r, cap: float):
    """The desk's governor and trip on the book's daily returns ``r``; returns a dict of what happened."""
    import numpy as np
    import pandas as pd

    eq, peaks, g = 1.0, [], 1.0
    gov, dd_path, trip = [], [], None
    for day, x in r.items():
        eq *= 1.0 + (0.0 if trip is not None else g) * x
        peaks.append(eq)
        peak = max(peaks[-WINDOW:])
        dd = 1.0 - eq / peak
        dd_path.append(dd)
        if trip is None and dd >= cap - 1e-12:
            trip = day
        g = 0.0 if trip is not None else float(np.clip((cap - dd) / (cap / 2.0), 0.0, 1.0))
        gov.append(g)
    gov = pd.Series(gov, index=r.index)
    before = gov if trip is None else gov.loc[:trip]
    cut = before[before < 1.0]
    return {"first_cut": cut.index[0].date() if len(cut) else None, "sessions_cut": int(len(cut)),
            "sessions": int(len(before)), "trip": trip.date() if trip is not None else None,
            "mean_gov": float(before.mean())}


def ungoverned(r, cap: float):
    """How often the ungoverned book's 250-close drawdown reached cap / 2 and cap, as separate episodes."""
    eq = (1.0 + r).cumprod()
    dd = 1.0 - eq / eq.rolling(WINDOW, min_periods=1).max()
    def episodes(level: float) -> int:
        above = dd >= level
        return int((above & ~above.shift(1, fill_value=False)).sum())
    return {"half": episodes(cap / 2.0), "cap": episodes(cap), "max_dd": float(dd.max())}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=None, help="also write the report here")
    a = ap.parse_args(argv)
    _setup()
    import numpy as np
    import pandas as pd

    rets, costs_version = strategy_returns()
    ran = {s: v for s, v in rets.items() if v[0] is not None}
    panel = pd.DataFrame({s: v[0] for s, v in ran.items()}).fillna(0.0)
    start = max(v[0].ne(0).idxmax() for v in ran.values())   # every running strategy has traded
    panel = panel.loc[start:]
    books = {"cash rows held (0.025 each)": panel.sum(axis=1) * FRACTION,
             f"rows that run rescaled to {BOOK_GROSS}": panel.sum(axis=1) * (BOOK_GROSS / len(ran))}
    from ohcamel_quant.data import fixtures

    spy_close = fixtures.history("SPY")[0]["close"]          # the bound: every slice long US equity
    books[f"reference: SPY held at {BOOK_GROSS}"] = spy_close.pct_change().reindex(panel.index).fillna(0.0) * BOOK_GROSS

    L = []
    L.append("INC-02 drawdown cap on the committed fixtures (tools/inc02_ddcap.py)")
    L.append(f"data: fixtures/history/*.parquet (Alpaca, unadjusted closes; dividends excluded), offline; "
             f"costs: research/config/costs.yaml v{costs_version}; window {panel.index[0].date()} .. "
             f"{panel.index[-1].date()} ({len(panel)} sessions)")
    L.append("rows:")
    for s, (r, tk, note) in rets.items():
        L.append(f"  {s:40s} {'RUN ' if r is not None else 'CASH'} {','.join(tk) or '-':28s} {note}")
    L.append(f"{len(ran)} of {len(rets)} rows run; {len(rets) - len(ran)} hold cash")
    L.append("")
    for name, r in books.items():
        vol = float(r.std(ddof=1) * np.sqrt(252))
        trailing = r.ewm(alpha=0.06).std() * np.sqrt(252)        # EWMA(0.94), the overlay's fallback forecaster
        bind = float((trailing > VOL_TARGET).mean())
        L.append(f"== {name}")
        L.append(f"   realised vol {vol:.4f} a year; one-year 2-sigma loss at that vol {2 * vol:.4f} "
                 f"(at the 0.10 vol target: 0.2000); EWMA(0.94) vol above 0.10 on {bind:.1%} of sessions")
        L.append("   cap    | ungoverned: episodes reaching cap/2, cap; worst 250-close dd | governed: first cut, "
                 "sessions cut, trip (book flat from then), mean governor to trip")
        for cap in CAPS:
            u, g = ungoverned(r, cap), governed(r, cap)
            L.append(f"   {cap:.2f}   | {u['half']:3d}, {u['cap']:3d}; {u['max_dd']:.4f}"
                     f"            | {g['first_cut'] or 'never'}, {g['sessions_cut']} of {g['sessions']}, "
                     f"{g['trip'] or 'never'}, {g['mean_gov']:.3f}")
        L.append("")
    text = "\n".join(L)
    print(text)
    if a.out is not None:
        a.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
