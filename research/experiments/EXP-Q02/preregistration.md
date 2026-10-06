# EXP-Q02 — Do HMM regime probabilities carry information about next-month risk?

**Status: APPROVED 2026-10-06.** Drafted by the agent 2026-10-06 for the
owner. Under `docs/CHARTER.md` the owner owns hypotheses; no result is
published until approval is recorded at the bottom of this file.

## What this is, and is not

A **descriptive risk model**, not a strategy. It produces regime
probabilities for Rates & Macro and the Front Page. It sizes nothing and
reaches no desk. Because it is not a strategy, the charter's strategy battery
does not apply to it directly; this file states what it is scored on instead.
A regime-switching *allocation* would be a separate experiment with the full
battery.

## Hypothesis (proposed — the owner's to accept, edit or reject)

The filtered (real-time) probability of the high-volatility state from a
Gaussian HMM on market and macro features predicts next-month realized SPY
volatility better than an EWMA(λ = 0.94) volatility forecast alone.

## Economic rationale

Volatility clusters and regimes persist (Hamilton 1989; Ang & Bekaert 2002):
credit spreads, curve slope and realized volatility move together into stress
states that last months. If the HMM only re-describes trailing volatility, the
EWMA baseline will match it — the expected failure mode, stated in advance.

## Design, fixed in advance

- **Features (weekly, point-in-time):** SPY 4-week return, SPY 4-week realized
  vol, 10y–2y slope (FRED DGS10 − DGS2), high-yield OAS change (FRED
  BAMLH0A0HYM2), each standardized with an expanding window only.
- **Model:** Gaussian HMM, own EM (`models/hmm.py`), states K ∈ {2, 3, 4}
  chosen by BIC **inside each training window**; 20 seeded restarts, best
  likelihood kept.
- **Evaluation:** expanding-window refits every 26 weeks from 2005; only the
  **filtered** probabilities at each date are scored (smoothed probabilities
  use the future and are displayed only as history, labelled as such).
- **Score:** out-of-sample regression of next-month SPY realized variance on
  EWMA forecast, with and without P(high-vol state); report the incremental
  R², the HAC t-stat on the probability term, and the QLIKE loss of both
  forecasts with a Diebold–Mariano test.
- **Holdout:** 2022-01 → latest, scored once.

## Pass criteria (proposed)

Holdout QLIKE improvement over EWMA with DM p < 0.05, and a HAC t-stat > 2 on
the probability term in the selection window. Otherwise the verdict is
"DESCRIPTIVE ONLY — adds nothing over EWMA", which still lets the page show
the regimes, labelled with that verdict.

## How it could fail

Label switching across refits (states are ordered by mean volatility to fix
it); a model that re-labels trailing volatility; the 2020 jump dominating the
fit; FRED revisions (OAS is not revised; the slope is from daily yields).

## Approval

`Approved-by:` owner (ajaiupadhyaya), 2026-10-06, approved as written, recorded by the agent from the owner's answer in session.
