# EXP-I03 — Does distance-method pairs trading on 30-minute bars survive friction among five liquid ETFs?

**Status: APPROVED 2026-10-11 for the design §5/§6 as written; the points
marked *not fixed by §6* below await the owner's confirmation, and the
battery does not run until it is recorded here.** Drafted by the agent
2026-10-11 for the owner. Under `docs/CHARTER.md` the owner owns hypotheses
and kill decisions; no result is published before that approval.

**Paper.** Gatev, E., Goetzmann, W. N. & Rouwenhorst, K. G. (2006), "Pairs
Trading: Performance of a Relative-Value Arbitrage Rule", *Review of
Financial Studies* 19(3), at the daily horizon on US stocks. The rule tested
here is the library's `pairs_distance`
(`quant/src/ohcamel_quant/backtest/strategies.py`), which implements that
paper's distance method, run on 30-minute bars: over a formation window,
normalise each name's price to 1 at its start and pick the pair with the
smallest sum of squared differences of normalised prices; in the following
trading window, open a position when the normalised spread is more than k
formation standard deviations from zero (short the rich leg, long the cheap
one) and close it when the spread crosses zero or the window ends.

**Owner's hypothesis.** Among SPY, QQQ, IWM, TLT and GLD, the pair whose
prices tracked each other most closely over the last 20 sessions converges
within the session often enough, after a 2σ or 2.5σ divergence, that the
rule survives retail friction.

**What was already seen, disclosed before this run.**
- **Not fixed by §6: §6 says "sector pairs"; this draft does not.** A five-name universe has no sectors.
  Pairs are formed among the five names by the distance method alone, and
  the number of pairs held is fixed in advance at 1 of the 10 possible.
- The paper's evidence is daily, on single stocks, 1962–2002. Nothing in it
  is evidence about 30-minute bars on index ETFs. The farm's daily
  `farm_pairs_distance_sectors` cell is incubated under INC-01; its record is
  not evidence here and is not read in drafting this.
- `bars_minute` has held Alpaca IEX minute bars for 50 names, these five
  among them, for about two years. No intraday strategy has been run on them
  in this repository, and the agent did not look at these names' intraday
  returns in drafting this. The holdout overlaps those two years and has
  been touched by no experiment.

**Economic rationale.** Liquidity provision to transient imbalances: a burst
of one-sided flow in one ETF (a large order, a creation or redemption, an
index rebalance) moves it away from a close substitute, and the trader who
needs immediacy pays the one who takes the other side until the gap closes.
The other side is that impatient trader. Among five names, the closest pair
will usually be two of the three equity indices; the experiment is then, in
practice, an intraday spread trade between them, and the mechanism is only
as good as their substitutability.

**Data, stated exactly.** Alpaca IEX 1-minute bars, regular session only,
from the earliest minute bar Alpaca serves for each name (expected 2016-01)
through 2026-09-30, warehouse table `bars_minute`. The selection window
starts 2016-06-01, or at the first full session after the name's first
minute bar plus the rule's warm-up if that is later (the rule's warm-up is its 20-session formation, counted from the latest of the five names' first bars); the
backfill records the first bar and the later of the two is written into
`config.yaml` before the battery runs. Minute bars are aggregated to
30-minute bars inside each session (13 in a full session, 7 in a 13:00
early close). Two facts bear on the result:
- **IEX is one venue,** a few percent of consolidated volume. A 30-minute
  bar's close is the last IEX trade in it, and two names' last trades can be
  minutes apart: a stale print looks like a divergence. The trade it
  triggers is filled at the next minute's price, after the stale name has
  caught up, so it is a noise trade that pays four fills. This works
  against the rule, and is stated now rather than found later.
- **The bars are unadjusted.** Formation spans 20 sessions, so a
  distribution's ex-date gap enters the normalised prices once; positions
  never span a night.

**Assumptions.**
- Selection window 2016-06-01 → 2023-12-31 (its start as stated under Data);
  holdout 2024-01-01 → 2026-09-30, evaluated once, with the configuration
  selected before it.
- One strategy over the five names, `exp_i03_pairs`.
- Formation is the trailing 20 sessions of 30-minute bars, re-formed before
  each session; the trading window is that one session. The battery calls
  `pairs_distance` once per session, on a slice of the 20 formation sessions
  plus that session, so the library's cycles align with sessions whatever
  their bar count. A position is
  decided on a 30-minute bar's close and filled at the next minute bar's
  open, and closes when the spread crosses zero or at the close of the
  session's last minute bar.
- `pairs_distance` holds one unit per leg; the weights are scaled to Σ|w| = 1,
  as INC-01's gross-scaling rule does on the desk, so each leg is half the
  allocation. Not fixed by §6.
- The battery's unit is the session: one net return per session on the
  allocated capital, annualised over 252 sessions; a session with no trade
  is 0.
- Friction model v1.0.0, `research/config/friction_v1.yaml`, per fill, as
  EXP-A01 ran it: per-symbol spread, halved per fill, plus regulatory fees,
  and ×1.5 spread widening when VIX (the previous session's close, the
  series EXP-A01 used) is above 25. Four fills per round trip.
- The charter's cost sweep, at 0, 5, 15 and 30 bps: at each level the spread
  is that many basis points round trip for every symbol, fees and the VIX
  widening stay, and the configuration selected at the base friction is
  **not** re-selected.
- Walk-forward over the selection window in 4 folds, as EXP-A01. DSR
  deflates by every configuration × fold: 2 × 4 = 8 fold-trials.

**Grid, fixed in advance.** `entry_sd ∈ {2.0, 2.5}` — two configurations.
Formation 20 sessions, `n_pairs` 1, exit at zero or the session's end: fixed,
not searched. Anything outside this grid is a new experiment, not a tweak.

**How it fails.** The pair is not stationary: equity indices diverge for
reasons that persist, and a spread that
widens and keeps widening is closed only by the session's end, at a loss.
Four fills per round trip on the many small divergences a 2σ trigger
admits; at 15 and 30 bps the cost sweep may erase it. Ten candidate pairs is
a thin universe, and the selected pair will rarely change. Stale IEX
prints add trades that are noise. Live, the session-end exit is a
market-on-close order sent at the producer's 15:45 mark, with the desk's
15:50 limit flatten only a backstop, so the strategy is traded to the close
as tested. Three regimes negative would refute it.

**Kill criteria.** Any charter gate failing is a fail: holdout positive;
DSR ≥ 0.30 over every configuration × fold; PSR ≥ 0.70; the stationary-block
bootstrap's lower 5th percentile above 0 (1000 resamples, seed 42); positive
in at least 3 of the research battery's five regimes (2018 Q4 selloff, 2020
COVID, 2022 rate shock, 2023 recovery, 2024); the cost sweep reported at
every level; PBO reported, and named in the verdict if above 0.5. A pass with
PBO above 0.5 is reported as "pass, fragile" and is not promoted without a
second, independent window. With two configurations, PBO is coarse, and
that is stated now. A verdict of pass makes the strategy eligible for `live`
sizing at the owner's word; any other verdict makes it eligible for
`incubation` at the owner's word. Promotion is the owner's decision alone
and never follows from the verdict by itself.

## Approval

Approved-by: owner (ajaiupadhyaya), 2026-10-11, approved the design §5/§6 in session ("Looks good, Everything approved"); recorded by the agent. A draft that departs from §5/§6 is not covered by this line.
