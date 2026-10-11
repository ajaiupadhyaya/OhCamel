# EXP-I02 — Does an opening-range breakout, stopped at the range's far side, survive friction on five liquid ETFs?

**Status: APPROVED 2026-10-11 for the design §5/§6 as written; the points
marked *not fixed by §6* below await the owner's confirmation, and the
battery does not run until it is recorded here.** Drafted by the agent
2026-10-11 for the owner. Under `docs/CHARTER.md` the owner owns hypotheses
and kill decisions; no result is published before that approval.

**Paper.** Zarattini, C., Aziz, A. & Barbon, A. (2023), "Can Day Trading
Really Be Profitable?", working paper: the opening-range breakout on QQQ.
Zarattini & Aziz (2024), a citation from memory for the owner to check,
follow it up on a universe of US stocks; single
stocks are out of scope here (the charter's survivorship stance). The rule
tested here: after the session's first R minutes, go long when price closes
above the range's high or short when it closes below its low, with a stop at
the range's other side, flat at the close, sized by volatility, with entry
and stop evaluated only at the producer's 15-minute marks.

**Owner's hypothesis.** On SPY, QQQ, IWM, TLT and GLD, a move out of the
opening range continues often and far enough, relative to the loss the stop
allows, that the rule survives retail friction.

**What was already seen, disclosed before this run.**
- The paper's sample, as the agent recalls it, runs on QQQ from 2016 into
  2023: almost the whole of this experiment's selection window. If so, for
  QQQ the selection window is not a first look in the literature's sense,
  and only the holdout (2024-01-01 → 2026-09-30) is. The owner should check
  the dates against the paper.
- This draft tests the breakout form §6 states. The paper's own entry, stop
  and profit-target conventions may differ in detail; where they do, the
  paper's are not tested here, and the owner should compare the two before
  approving.
- `bars_minute` has held Alpaca IEX minute bars for 50 names, these five
  among them, for about two years. No intraday strategy has been run on them
  in this repository, and the agent did not look at these names' intraday
  returns in drafting this. The holdout overlaps those two years and has
  been touched by no experiment.

**Economic rationale.** The open is where overnight information is priced. A
close outside the first minutes' range is read as the direction that pricing
resolves in; the stop at the range's far side fixes the loss if it does not,
so the rule is a bet that winners run further than the range is wide. The
other side is the opening's liquidity providers and the traders who fade the
first move. **This mechanism is weaker than EXP-I01's:** it says when the
rule should work, not who loses to it and why they keep doing so. That is
stated now, as the reason a fail here is the expected result.

**Data, stated exactly.** Alpaca IEX 1-minute bars, regular session only,
from the earliest minute bar Alpaca serves for each name (expected 2016-01)
through 2026-09-30, warehouse table `bars_minute`. The selection window
starts 2016-06-01, or at the first full session after the name's first
minute bar plus the rule's warm-up if that is later (the rule's warm-up is the 14 sessions of its ATR); the
backfill records the first bar and the later of the two is written into
`config.yaml` before the battery runs. Two facts bear on the result:
- **IEX is one venue,** a few percent of consolidated volume. Its minute
  highs and lows are narrower than the tape's, so the opening range is
  narrower and breakouts more frequent than on consolidated data, and a
  minute with no IEX trade has no bar. A session with no bar in the opening
  range is flat.
- **The rule reads the bars at the producer's marks only:** at :00, :15,
  :30 and :45, on the minute-bar closes available then, never tick by tick
  and never between marks, so the test sees exactly what the live producer
  will.
- **The bars are unadjusted.** No position is held overnight, so a
  distribution's ex-date gap is never earned or lost; it enters that
  session's true range, overstating the ATR slightly and so sizing slightly
  smaller for 14 sessions.

**Assumptions.**
- Selection window 2016-06-01 → 2023-12-31 (its start as stated under Data);
  holdout 2024-01-01 → 2026-09-30, evaluated once, with the configuration
  selected before it.
- One strategy per name (`exp_i02_spy`, `_qqq`, `_iwm`, `_tlt`, `_gld`).
  Not fixed by §6.
- The opening range is the high and low of the minute bars from 09:30 to
  09:30 + R. Entry and stop are evaluated only at the producer's 15-minute
  marks (:00, :15, :30, :45), from the first mark at or after the range ends
  through 15:30, on the last complete minute bar's close at that mark: a
  close above the high (below the low) is the signal, filled at the next
  minute bar's open. One entry per session: after a stop, flat for the rest
  of the session. The stop is checked at the same marks: a close at or
  beyond the range's other side exits at the next minute bar's open, so a
  loss can exceed the range. Otherwise the position closes at the session's
  close (the 15:45 mark sends a market-on-close order). An early-close
  session trades the same rule to its own close; not fixed by §6.
- **Volatility sizing** (not fixed by §6): |w| = min(1, 0.01 / (ATR₁₄ / the
  prior session's close)), where ATR₁₄ is the 14-session average true range
  of regular-session daily bars built from the same minute bars. A move of
  one daily ATR against the position costs about 1 % of the allocated
  capital; the position never exceeds the allocation.
- The battery's unit is the session: one net return per session on the
  allocated capital, annualised over 252 sessions; a session with no trade
  is 0.
- Friction model v1.0.0, `research/config/friction_v1.yaml`, per fill, as
  EXP-A01 ran it: per-symbol spread, halved per fill, plus regulatory fees,
  and ×1.5 spread widening when VIX (the previous session's close, the
  series EXP-A01 used) is above 25. Two fills per trade; a stopped trade
  pays the same.
- The charter's cost sweep, at 0, 5, 15 and 30 bps: at each level the spread
  is that many basis points round trip for every symbol, fees and the VIX
  widening stay, and the configuration selected at the base friction is
  **not** re-selected.
- Walk-forward over the selection window in 4 folds, as EXP-A01. **DSR
  deflates by every configuration × fold across all five names, in one
  unit: 3 × 4 × 5 = 60 fold-trials.** Not fixed by §6.

**Grid, fixed in advance.** `range_minutes ∈ {5, 15, 30}` — three
configurations per name, each with entry and stop evaluated only at the
15-minute marks as above; that is the rule under test, not an approximation
of a tick-by-tick one. The stop, the sizing and the exit are fixed above and
are not searched. Anything outside this grid is a new experiment, not a
tweak.

**How it fails.** Range days: price leaves the range, reverses through it
and stops out, twice the spread paid for a loss. A 5-minute range on IEX bars
is narrow, so stops are close and frequent. The paper's QQQ result may not
extend to TLT and GLD, whose opens carry less overnight equity news.
Evaluating only at 15-minute marks makes a 5-minute range act later than the
paper's rule and lets a stop overshoot the range's far side; that is the
rule tested, and the price of trading as tested. The exit is a
market-on-close order sent at the 15:45 mark, with the desk's 15:50 limit
flatten only a backstop. Three regimes negative would refute it.

**Kill criteria.** Any charter gate failing is a fail: holdout positive;
DSR ≥ 0.30 over every configuration × fold; PSR ≥ 0.70; the stationary-block
bootstrap's lower 5th percentile above 0 (1000 resamples, seed 42); positive
in at least 3 of the research battery's five regimes (2018 Q4 selloff, 2020
COVID, 2022 rate shock, 2023 recovery, 2024); the cost sweep reported at
every level; PBO reported, and named in the verdict if above 0.5. A pass with
PBO above 0.5 is reported as "pass, fragile" and is not promoted without a
second, independent window. A verdict of pass makes a strategy eligible for
`live` sizing at the owner's word; any other verdict makes it eligible for
`incubation` at the owner's word. Promotion is the owner's decision alone and
never follows from the verdict by itself.

## Approval

Approved-by: owner (ajaiupadhyaya), 2026-10-11, approved the design §5/§6 in session ("Looks good, Everything approved"); recorded by the agent. A draft that departs from §5/§6 is not covered by this line.
