# EXP-I01 — Does the first half-hour's return predict the last half-hour's on five liquid ETFs, after friction?

**Status: APPROVED 2026-10-11 for the design §5/§6 as written; the points
marked *not fixed by §6* below await the owner's confirmation, and the
battery does not run until it is recorded here.** Drafted by the agent
2026-10-11 for the owner. Under `docs/CHARTER.md` the owner owns hypotheses
and kill decisions; no result is published before that approval.

**Paper.** Gao, L., Han, Y., Li, S. Z. & Zhou, G. (2018), "Market intraday
momentum", *Journal of Financial Economics* 129(2). The finding: the S&P 500
ETF's first half-hour return predicts its last half-hour return, in sign.
The rule tested here: take a position in the sign of the session's
09:30–10:00 return at 15:30, and be flat at the close.

**Owner's hypothesis.** On SPY, QQQ, IWM, TLT and GLD, the sign of the first
half-hour return predicts the sign of the last half-hour return often enough
that a one-trade-a-day rule on it survives retail friction.

**What was already seen, disclosed before this run.**
- The paper's sample is SPY, 1993–2013, and ends before this experiment's
  window. The effect is tested here because it was published; a published
  effect is a selected one, and post-publication decay is the first thing
  this experiment can show.
- **The paper's first half-hour return runs from the previous session's
  close to 10:00**, so it includes the overnight return. §6 states the
  09:30–10:00 return, and that is what is tested here (regular session
  only). It is a narrower signal than the paper's; the owner should confirm
  it, or change it before the battery runs, not after.
- `bars_minute` has held Alpaca IEX minute bars for 50 names, these five
  among them, for about two years (top-ups every 30 minutes in session). No
  intraday strategy has been run on them in this repository, and the agent
  did not look at these names' intraday returns in drafting this. The
  holdout, 2024-01-01 → 2026-09-30, overlaps those two years and has been
  touched by no experiment.

**Economic rationale.** Two mechanisms, both with a named other side. Late-
informed traders and institutions that trade on the morning's news at the
close continue the morning's move (the paper's account). Hedgers whose
exposure grows with the day's move — short-gamma option dealers and
leveraged-ETF rebalancing — trade in its direction near the close
(Baltussen, Da, Lammers & Martens 2021, "Hedging demand and market intraday
momentum", *Journal of Financial Economics* 142(1)). Both are equity-index
stories; for TLT and GLD the mechanism is weaker, and that is stated here
rather than found later.

**Data, stated exactly.** Alpaca IEX 1-minute bars, regular session only,
from the earliest minute bar Alpaca serves for each name (expected 2016-01)
through 2026-09-30, warehouse table `bars_minute`. The selection window
starts 2016-06-01, or at the first full session after the name's first
minute bar plus the rule's warm-up if that is later (the rule needs no warm-up beyond the session itself); the
backfill records the first bar and the later of the two is written into
`config.yaml` before the battery runs. Two facts bear on the result:
- **IEX is one venue,** a few percent of consolidated volume. Its minute
  prices are noisier than the tape's, and a minute with no IEX trade has no
  bar. A window's price is the last bar inside it; a session with no bar in
  the first half-hour or none in the minute of entry is flat.
- **The bars are unadjusted.** Within one session this changes nothing: the
  rule never holds overnight.

**Assumptions.**
- Selection window 2016-06-01 → 2023-12-31 (its start as stated under Data);
  holdout 2024-01-01 → 2026-09-30, evaluated once, with the configuration
  selected before it.
- One strategy per name (`exp_i01_spy`, `_qqq`, `_iwm`, `_tlt`, `_gld`), as
  EXP-A01 has one per name. Each is long or short its whole allocation
  (Σ|w| = 1) or flat. Not fixed by §6.
- The first half-hour return is the close of the bar ending 10:00 over the
  open of the 09:30 bar, less 1. The position is decided on the bar closing
  at 15:30 and filled at the next minute bar's open; it is closed at the
  close of the session's last minute bar. A zero first half-hour return is
  flat. An early-close session is flat (its 15:30 does not exist) and counts
  as a session with return 0; not fixed by §6.
- The battery's unit is the session: one net return per session on the
  allocated capital, annualised over 252 sessions.
- Friction model v1.0.0, `research/config/friction_v1.yaml`, per fill, as
  EXP-A01 ran it: per-symbol spread, halved per fill, plus regulatory fees,
  and ×1.5 spread widening when VIX (the previous session's close, the
  series EXP-A01 used) is above 25. Two fills a session.
- The charter's cost sweep, at 0, 5, 15 and 30 bps: at each level the spread
  is that many basis points round trip for every symbol, fees and the VIX
  widening stay, and the configuration selected at the base friction is
  **not** re-selected.
- Walk-forward over the selection window in 4 folds, as EXP-A01. **DSR
  deflates by every configuration × fold across all five names, in one
  unit: 2 × 4 × 5 = 40 fold-trials.** Five names tested is five tries at the
  same idea, and the deflation says so. Not fixed by §6.

**Grid, fixed in advance.** `condition ∈ {none, last_half_hour_agrees}`:
unconditional; or traded only when the 15:00–15:30 return has the same sign
as the first half-hour return (flat otherwise). Two configurations per name.
Anything outside this grid is a new experiment, not a tweak.

**How it fails.** The effect may have decayed since 2013; the holdout is the
most recent and the most likely to show it. The expected return per trade is
a few basis points against two fills a day, so the 15 and 30 bps cost levels
may erase it by arithmetic alone. TLT and GLD may show nothing, since the
hedging mechanism is an equity-index one. Live, the strategy enters on the
producer's 15:30 document and exits by a market-on-close order (Alpaca
`cls`) sent when the producer's 15:45 zero-target document arrives; the
desk's 15:50 limit flatten is only a backstop when no `cls` order rests. So
it is traded as tested, to the close; a 15:30 document that reaches the desk
late shortens the holding, and the live record shows the actual entry
times. Three regimes negative would refute it.

**Kill criteria.** Any charter gate failing is a fail: holdout positive;
DSR ≥ 0.30 over every configuration × fold; PSR ≥ 0.70; the stationary-block
bootstrap's lower 5th percentile above 0 (1000 resamples, seed 42); positive
in at least 3 of the research battery's five regimes (2018 Q4 selloff, 2020
COVID, 2022 rate shock, 2023 recovery, 2024); the cost sweep reported at
every level; PBO reported, and named in the verdict if above 0.5. A pass with
PBO above 0.5 is reported as "pass, fragile" and is not promoted without a
second, independent window. With two configurations per name, PBO is coarse,
and that is stated now. A verdict of pass makes a strategy eligible for
`live` sizing at the owner's word; any other verdict makes it eligible for
`incubation` at the owner's word. Promotion is the owner's decision alone and
never follows from the verdict by itself.

## Approval

Approved-by: owner (ajaiupadhyaya), 2026-10-11, approved the design §5/§6 in session ("Looks good, Everything approved"); recorded by the agent. A draft that departs from §5/§6 is not covered by this line.
